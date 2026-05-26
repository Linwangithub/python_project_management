"""Nginx server 块解析、生成和替换工具。

本模块集中维护 server 块范围查找、listen/proxy_pass 端口解析、项目配置块生成、
配置块归一化、替换追加和删除逻辑。调用方只负责读取和写入配置文件。
"""

from __future__ import annotations

import re

from fastapi import HTTPException

from app.utils.pspm.project_config import FRONTEND_DIST_BASE_DIR, NETWORK_PORT_MAX, NETWORK_PORT_MIN
from app.utils.pspm.nginx_inventory import _strip_nginx_comments


def _find_server_block_ranges(text: str) -> list[tuple[int, int]]:
  """查找 Nginx 配置中所有 server 块的字符范围。"""
  # ranges 保存每个 server 块在原始配置文本中的起止索引。
  # 后续替换/删除必须依赖原始索引，不能只返回块文本，否则会丢失缩进和块外内容。
  ranges: list[tuple[int, int]] = []
  # 只匹配行首或换行后的 server {，避免误把注释、变量名或 location 内容当成 server 块。
  pattern = re.compile(r'(^|\n)\s*server\s*\{')
  for match in pattern.finditer(text):
    start = match.start(0)
    brace_start = text.find('{', match.start(0), match.end(0) + 4)
    if brace_start < 0:
      continue
    # 通过花括号深度计数查找当前 server 块的真正结束位置。
    # Nginx server 内部可能还有 location 等子块，不能简单查找第一个右花括号。
    depth = 0
    end = -1
    i = brace_start
    while i < len(text):
      ch = text[i]
      if ch == '{':
        depth += 1
      elif ch == '}':
        depth -= 1
        if depth == 0:
          end = i
          break
      i += 1
    if end > brace_start:
      ranges.append((start, end + 1))
  return ranges

def _server_block_contains_project(block_text: str, project_name: str) -> bool:
  """判断 server 块是否属于指定项目。"""
  # 每个由系统生成/确认的项目 server 块都会写入 # pspm_project 项目标记。
  # 删除或替换配置时只操作带该标记的块，避免误删用户手写的其他 Nginx 配置。
  name = re.escape(project_name)
  return re.search(rf'(?m)^\s*#\s*pspm_project\s+{name}\s*$', block_text) is not None

def _server_block_listen_ports(block_text: str) -> set[int]:
  """提取 server 块中的 listen 端口集合。"""
  ports: set[int] = set()
  # listen 可能写成 listen 80、listen 0.0.0.0:80、listen [::]:80 default_server。
  # 因此先取整条 listen 参数，再从参数中提取 2~5 位端口。
  for m in re.finditer(r'(?m)^\s*listen\s+([^;]+);', block_text):
    raw = m.group(1)
    hit = re.search(r'(?<!\d)(\d{2,5})(?!\d)', raw)
    if not hit:
      continue
    try:
      p = int(hit.group(1))
    except Exception:
      continue
    if NETWORK_PORT_MIN <= p <= NETWORK_PORT_MAX:
      ports.add(p)
  return ports

def _server_block_proxy_pass_ports(block_text: str) -> set[int]:
  """提取 server 块中 proxy_pass 指向的端口集合。"""
  ports: set[int] = set()
  # proxy_pass 端口判断必须忽略注释，否则注释里的历史端口会被误判为真实后端部署端口。
  clean = _strip_nginx_comments(block_text)
  for m in re.finditer(r'(?m)^\s*proxy_pass\s+([^;]+);', clean):
    raw = m.group(1).strip()
    for hit in re.finditer(r':(\d{2,5})(?:/|$|[^0-9])', raw):
      try:
        p = int(hit.group(1))
      except Exception:
        continue
      if NETWORK_PORT_MIN <= p <= NETWORK_PORT_MAX:
        ports.add(p)
  return ports

def _build_project_nginx_server_block(
  project_name: str,
  frontend_port: int,
  backend_port: int,
  backend_ip: str,
  nginx_server_ip: str,
  username: str,
  frontend_root: str = '',
) -> str:
  """生成项目默认 Nginx server block。"""
  # 项目名会写进注释标记，先做安全化处理，避免特殊字符破坏 Nginx 配置结构。
  safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', project_name)
  # username 当前仅保留兼容老调用，项目静态资源路径已经统一由 frontend_root 显式传入或配置默认值生成。
  _safe_username = re.sub(r'[^a-zA-Z0-9_\-]', '_', username or 'root')
  # 新建或设置 Nginx 时，如果前端没有传静态资源目录，则使用全局默认前端打包根目录。
  frontend_root = str(frontend_root or '').strip() or f'{FRONTEND_DIST_BASE_DIR}/{project_name}'
  return (
    f"server {{\n"
    f"    listen       {frontend_port};\n"
    f"    server_name  {nginx_server_ip};\n"
    f"    # pspm_project {safe_name}\n"
    f"\n"
    f"    location / {{\n"
    f"        root   {frontend_root};\n"
    f"        index  index.html index.htm;\n"
    f"    }}\n"
    f"\n"
    f"    location /api {{\n"
    f"        proxy_pass   http://{backend_ip}:{backend_port}/api;\n"
    f"        add_header 'Access-Control-Allow-Origin' '*';\n"
    f"        add_header 'Access-Control-Allow-Credentials' 'true';\n"
    f"        proxy_buffering off;\n"
    f"        #proxy_set_header Connection \"\";\n"
    f"        client_body_buffer_size 4096m;\n"
    f"        client_max_body_size 4096m;\n"
    f"        proxy_max_temp_file_size 4096m;\n"
    f"        proxy_send_timeout 1800;\n"
    f"        proxy_read_timeout 1800;\n"
    f"        proxy_next_upstream http_500 http_504 http_502 error timeout invalid_header;\n"
    f"    }}\n"
    f"\n"
    f"    error_page 404 /404.html;\n"
    f"\n"
    f"    location = /40x.html {{\n"
    f"    }}\n"
    f"}}\n"
  )

def _normalize_confirmed_nginx_server_block(
  block_text: str,
  project_name: str,
  frontend_port: int,
  backend_port: int,
) -> str:
  """校验并标准化前端确认过的 Nginx server block。"""
  # 前端预览弹框确认后的完整 server block 会传到这里。
  # 后端仍要重新校验，避免绕过前端直接提交非法 Nginx 配置。
  text = str(block_text or '').strip()
  if not text:
    raise HTTPException(status_code=400, detail='请先确认Nginx详细配置')
  if not _find_server_block_ranges(text):
    raise HTTPException(status_code=400, detail='Nginx详细配置必须包含 server 块')
  # 一个提交文本理论上可能包含多个 server 块。
  # 这里合并所有 listen/proxy_pass 端口，只要包含用户确认的前端端口和后端部署端口即可放行。
  listen_ports: set[int] = set()
  proxy_ports: set[int] = set()
  for start, end in _find_server_block_ranges(text):
    block = text[start:end]
    listen_ports.update(_server_block_listen_ports(block))
    proxy_ports.update(_server_block_proxy_pass_ports(block))
  if int(frontend_port) not in listen_ports:
    raise HTTPException(status_code=400, detail=f'Nginx详细配置必须包含 listen {frontend_port}')
  if int(backend_port) not in proxy_ports:
    raise HTTPException(status_code=400, detail=f'Nginx详细配置必须包含 proxy_pass 后端端口 {backend_port}')

  # 归一化项目标记：历史版本可能写成 pspm_project xxx;，新版本统一使用注释形式。
  # 该标记是后续替换/删除当前项目 server block 的唯一依据。
  safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', project_name)
  if re.search(rf'(?m)^\s*pspm_project\s+{re.escape(safe_name)}\s*;', text):
    text = re.sub(rf'(?m)^\s*pspm_project\s+{re.escape(safe_name)}\s*;\s*$', f'    # pspm_project {safe_name}', text, count=1)
  elif not re.search(rf'(?m)^\s*#\s*pspm_project\s+{re.escape(safe_name)}\s*$', text):
    text = re.sub(r'(server\s*\{)', f'\\1\n    # pspm_project {safe_name}', text, count=1)
  if not text.endswith('\n'):
    text += '\n'
  return text

def _replace_or_append_project_server_block(conf_text: str, project_name: str, new_block: str) -> str:
  """替换或追加指定项目的 Nginx server block。"""
  # 先在已有配置中查找当前项目标记；找到则原地替换，保持其他项目和用户配置不变。
  ranges = _find_server_block_ranges(conf_text)
  for start, end in ranges:
    block = conf_text[start:end]
    if _server_block_contains_project(block, project_name):
      return conf_text[:start] + new_block + conf_text[end:]
  # 未找到旧项目块时走追加逻辑。
  # 追加前保证原文件末尾有换行，避免新 server 块贴在上一条配置后面。
  if conf_text and not conf_text.endswith('\n'):
    conf_text += '\n'
  return conf_text + '\n' + new_block

def _remove_project_server_blocks(conf_text: str, project_name: str) -> tuple[str, int]:
  """从 Nginx 配置文本中删除指定项目的 server block。"""
  ranges = _find_server_block_ranges(conf_text)
  if not ranges:
    return conf_text, 0
  # 使用分段拼接删除目标块：
  # keep_parts 保存每个“非目标 server 块之间的原始文本”，最大程度保留用户配置格式。
  keep_parts: list[str] = []
  last = 0
  removed = 0
  for start, end in ranges:
    block = conf_text[start:end]
    if _server_block_contains_project(block, project_name):
      keep_parts.append(conf_text[last:start])
      last = end
      removed += 1
  keep_parts.append(conf_text[last:])
  if removed == 0:
    return conf_text, 0
  return ''.join(keep_parts), removed
