"""Nginx 配置文件清单解析工具。

本模块集中维护本地 Nginx 主配置 include 解析、可选配置文件发现、可新建配置目录推导等纯工具逻辑。
远端服务器的清单收集仍由 nginx_utils 中的远端脚本保持兼容，避免重构时改变线上行为。
"""

from __future__ import annotations

import glob
import os
import re


def _strip_nginx_comments(text: str) -> str:
  """移除 Nginx 配置中的注释文本。

  参数：
  - text：Nginx 配置文件内容。

  作用：
  - 后续解析 include、server、listen、proxy_pass 时避免被注释内容干扰。
  """
  lines = []
  # Nginx 的 # 注释只有在引号外才生效；例如 proxy_set_header X '#'; 不能被截断。
  # 因此逐字符维护单双引号状态，而不是直接按 # split。
  for line in str(text or '').splitlines():
    in_single = False
    in_double = False
    cut_at = -1
    for idx, ch in enumerate(line):
      if ch == "'" and not in_double:
        in_single = not in_single
      elif ch == '"' and not in_single:
        in_double = not in_double
      elif ch == '#' and not in_single and not in_double:
        cut_at = idx
        break
    lines.append(line if cut_at < 0 else line[:cut_at])
  return '\n'.join(lines)

def _resolve_nginx_include_pattern(pattern: str, base_dir: str) -> str:
  """把 include 规则解析为绝对路径或 glob 规则。"""
  raw = str(pattern or '').strip().strip('"').strip("'")
  if not raw:
    return ''
  if raw.startswith('/'):
    return raw
  return os.path.normpath(os.path.join(base_dir, raw))

def _extract_direct_include_patterns(conf_text: str) -> list[str]:
  """提取 Nginx 主配置顶层 include 规则。"""
  clean = _strip_nginx_comments(conf_text)
  result: list[str] = []
  # depth 表示当前所处的大括号层级。
  # 这里只取 depth==0 的 include，http 块内 include 会由 _extract_nginx_include_patterns_by_scope 单独处理。
  depth = 0
  for line in clean.splitlines():
    line_text = line.strip()
    if depth == 0:
      m = re.match(r'^include\s+([^;]+);', line_text)
      if m:
        result.append(m.group(1).strip())
    depth += line.count('{') - line.count('}')
    if depth < 0:
      depth = 0
  return result

def _find_nginx_named_block_inner_ranges(text: str, block_name: str) -> list[tuple[int, int]]:
  """查找指定 Nginx 块内部文本范围。

  参数：
  - text：完整配置文本。
  - block_name：块名称，例如 `http`。

  返回：
  - 每个块内部 `(start, end)` 字符索引范围。
  """
  ranges: list[tuple[int, int]] = []
  clean = _strip_nginx_comments(text)
  # 对注释清理后的文本找命名块，避免注释中的 http { 或 events { 干扰范围计算。
  pattern = re.compile(rf'(^|\n)\s*{re.escape(block_name)}\s*\{{')
  for match in pattern.finditer(clean):
    brace_start = clean.find('{', match.start(0), match.end(0) + 4)
    if brace_start < 0:
      continue
    depth = 0
    end = -1
    i = brace_start
    while i < len(clean):
      ch = clean[i]
      if ch == '{':
        depth += 1
      elif ch == '}':
        depth -= 1
        if depth == 0:
          end = i
          break
      i += 1
    if end > brace_start:
      ranges.append((brace_start + 1, end))
  return ranges

def _extract_nginx_include_patterns_by_scope(conf_text: str) -> list[dict[str, str]]:
  """提取顶层和 http 块中的 include 规则。"""
  result: list[dict[str, str]] = []
  # source 字段用于前端区分 include 来源，也方便后续排查“为什么这个配置文件会出现在列表里”。
  for pattern in _extract_direct_include_patterns(conf_text):
    result.append({'pattern': pattern, 'source': 'top'})

  clean = _strip_nginx_comments(conf_text)
  for start, end in _find_nginx_named_block_inner_ranges(clean, 'http'):
    for pattern in _extract_direct_include_patterns(clean[start:end]):
      result.append({'pattern': pattern, 'source': 'http'})
  return result

def _nginx_base_dir_from_path(path: str) -> str:
  """从配置文件路径推导到 nginx 根目录层级。"""
  normalized = os.path.normpath(str(path or '').strip())
  if not normalized.startswith('/'):
    return ''
  parts = normalized.strip('/').split('/')
  # 常见 Nginx 安装路径是 /etc/nginx 或 /opt/nginx。
  # 找到 nginx 目录层级后，前端新建配置文件时可展示相对该根目录的文件夹。
  for idx, part in enumerate(parts):
    if part == 'nginx':
      return '/' + '/'.join(parts[:idx + 1])
  return os.path.dirname(normalized)

def _include_pattern_directory(pattern: str) -> str:
  """从 include 规则中提取目录部分。"""
  normalized = os.path.normpath(str(pattern or '').strip())
  # include 规则可能是 /opt/nginx/conf/conf.d/*.conf。
  # 如果带通配符，取通配符前面的目录作为可新建配置文件目录。
  wildcard_positions = [pos for pos in [normalized.find('*'), normalized.find('?'), normalized.find('[')] if pos >= 0]
  if wildcard_positions:
    prefix = normalized[:min(wildcard_positions)]
    directory = os.path.dirname(prefix.rstrip('/')) if not prefix.endswith('/') else prefix.rstrip('/')
  else:
    directory = os.path.dirname(normalized)
  return os.path.normpath(directory) if directory else ''

def _is_nginx_modules_path(path: str) -> bool:
  """判断路径是否是 Nginx modules 目录。"""
  normalized = str(path or '').strip().replace('\\', '/')
  return normalized == '/usr/share/nginx/modules' or normalized.startswith('/usr/share/nginx/modules/')

def _nginx_include_pattern_allows_conf(pattern: str) -> bool:
  """判断 include 规则是否允许普通 .conf 配置文件。"""
  raw = str(pattern or '').strip().replace('\\', '/')
  # modules 目录里的 .conf 通常是动态模块加载配置，不应该作为项目 server block 候选。
  if _is_nginx_modules_path(raw):
    return False
  base_name = os.path.basename(raw).lower()
  if not base_name.endswith('.conf'):
    return False
  return True

def _is_nginx_conf_include_candidate(pattern: str) -> bool:
  """判断 include 规则是否适合作为项目 server block 写入候选。"""
  raw = str(pattern or '').strip().replace('\\', '/')
  if _is_nginx_modules_path(raw):
    return False
  base_name = os.path.basename(raw).lower()
  if not base_name.endswith('.conf'):
    return False
  if any(token in base_name for token in ['*', '?', '[']):
    return False
  return True

def _collect_nginx_conf_inventory(main_conf_path: str) -> dict[str, list[dict[str, str]]]:
  """收集当前后端服务器上的 Nginx 配置文件清单。

  参数：
  - main_conf_path：正在运行的 Nginx 主配置文件。

  作用：
  - 解析主配置顶层 include 和 http 块 include。
  - 返回可选择的已有配置文件和可新建配置文件目录。
  """
  main_conf = os.path.normpath(str(main_conf_path or '').strip())
  if not main_conf or not os.path.isfile(main_conf):
    return {'conf_files': [], 'new_conf_dirs': []}

  # conf_files 给前端“使用已有配置文件”下拉框使用。
  # new_conf_dirs 给前端“新建配置文件”目录选择使用。
  conf_files: list[dict[str, str]] = []
  new_conf_dirs: list[dict[str, str]] = []
  seen_files: set[str] = set()
  seen_patterns: set[str] = set()
  seen_dirs: set[tuple[str, str]] = set()

  def add_file(path: str, source: str, include_pattern: str = ''):
    """把一个真实存在的 .conf 文件加入可选配置文件列表。"""
    # 真实存在的配置文件才可作为 selectable=True 的候选项。
    # seen_files 用于去重，避免同一个文件被多个 include 规则重复展示。
    normalized = os.path.normpath(str(path or '').strip())
    if not normalized or normalized in seen_files or not os.path.isfile(normalized):
      return
    if _is_nginx_modules_path(normalized):
      return
    seen_files.add(normalized)
    conf_files.append({
      'path': normalized,
      'source': source,
      'include_pattern': include_pattern,
      'kind': 'file',
      'selectable': True,
      'status': 'available',
    })

  def add_include_pattern(pattern: str, source: str):
    """记录 include 规则本身；带通配符的规则只展示为不可选项。"""
    # 历史前端会展示 include 规则本身，但不能直接选择通配符路径写入。
    # 因此保留 disabled 项用于说明来源，同时真正可选的是 glob 匹配出来的具体文件。
    normalized = os.path.normpath(str(pattern or '').strip())
    if not _is_nginx_conf_include_candidate(normalized):
      return
    if not normalized or normalized in seen_patterns:
      return
    seen_patterns.add(normalized)
    conf_files.append({
      'path': normalized,
      'source': source,
      'include_pattern': normalized,
      'kind': 'include_pattern',
      'selectable': False,
      'status': 'disabled',
    })

  def add_new_dir(resolved_pattern: str, source: str):
    """根据 include 规则推导可新建 .conf 文件的目录。"""
    if not _nginx_include_pattern_allows_conf(resolved_pattern):
      return
    # 只有 include 规则指向已存在目录时，才允许用户在该目录下新建项目 .conf 文件。
    directory = _include_pattern_directory(resolved_pattern)
    if not directory or not os.path.isdir(directory):
      return
    base_dir = _nginx_base_dir_from_path(directory)
    if not base_dir:
      return
    key = (base_dir, directory)
    if key in seen_dirs:
      return
    seen_dirs.add(key)
    rel = os.path.relpath(directory, base_dir)
    folder_name = '' if rel == '.' else rel.split(os.sep)[0]
    label = f'{folder_name} ({directory})' if folder_name else directory
    new_conf_dirs.append({
      'base_dir': base_dir,
      'directory': directory,
      'folder_name': folder_name,
      'include_pattern': resolved_pattern,
      'source': source,
      'label': label,
      'status': 'available',
    })

  add_file(main_conf, 'main')
  try:
    with open(main_conf, 'r', encoding='utf-8', errors='replace') as f:
      text = f.read()
  except Exception:
    return {'conf_files': conf_files, 'new_conf_dirs': new_conf_dirs}

  # 相对 include 以主配置文件所在目录为基准解析，这是 Nginx 的常见行为。
  base_dir = os.path.dirname(main_conf)
  for item in _extract_nginx_include_patterns_by_scope(text):
    pattern = str(item.get('pattern') or '').strip()
    source = str(item.get('source') or '').strip() or 'include'
    resolved_pattern = _resolve_nginx_include_pattern(pattern, base_dir)
    if not resolved_pattern:
      continue
    # 同一条 include 规则同时承担两个职责：
    # 1. 推导可新建配置文件目录；2. 展开已有 .conf 文件供同步/设置选择。
    add_new_dir(resolved_pattern, source)
    for matched in sorted(glob.glob(resolved_pattern)):
      if os.path.isfile(matched) and matched.endswith('.conf'):
        add_file(matched, source, resolved_pattern)

  return {'conf_files': conf_files, 'new_conf_dirs': new_conf_dirs}

def _collect_nginx_conf_files(main_conf_path: str) -> list[dict[str, str]]:
  """兼容旧调用：只返回已有 Nginx 配置文件列表。"""
  return _collect_nginx_conf_inventory(main_conf_path).get('conf_files', [])
