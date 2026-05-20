import glob
import json
import os
import re
import shlex

from fastapi import HTTPException

from app.utils.pspm.shell_utils import _is_local_server_ip, _run_server_shell, _run_shell, _split_lines


async def _is_port_in_use(port: int) -> bool:
  """检查当前后端服务器的端口是否被系统监听占用。

  参数：
  - port：要检测的端口。

  作用：
  - 创建项目和设置项目时，检测 Nginx 前端端口或后端部署端口是否已被系统进程占用。

  返回：
  - True 表示端口已被占用；False 表示端口未监听。
  """
  check_cmd = (
    "ss -lntH | awk '{print $4}' "
    "| sed -E 's#.*:([0-9]+)$#\\1#' "
    f"| grep -x {int(port)} >/dev/null 2>&1"
  )
  code, _out, _err = await _run_shell(check_cmd, timeout=10)
  return code == 0


async def _is_nginx_running() -> bool:
  """检查当前后端服务器上的 Nginx 是否运行。"""
  code, _out, _err = await _run_shell('pgrep -x nginx >/dev/null 2>&1', timeout=10)
  return code == 0


def _parse_nginx_conf_path(text: str) -> str:
  """从 `nginx -V` 输出中解析编译时默认配置文件路径。"""
  m = re.search(r'--conf-path=([^\s]+)', text or '')
  if m:
    return m.group(1).strip()
  return ''


async def _get_running_nginx_conf_path() -> str:
  """获取当前后端服务器正在运行的 Nginx 主配置文件路径。

  作用：
  - 优先从 `pgrep -a -x nginx` 的 `-c` 参数读取运行中配置文件。
  - 如果进程参数没有指定 `-c`，再从 `nginx -V` 的 `--conf-path` 读取默认路径。
  """
  code, out, _err = await _run_shell("pgrep -a -x nginx | head -n 1", timeout=10)
  if code == 0 and out.strip():
    line = out.strip()
    m = re.search(r'\s-c\s+([^\s]+)', line)
    if m:
      return m.group(1).strip()

  _code2, out2, err2 = await _run_shell('nginx -V 2>&1', timeout=10)
  merged = '\n'.join(_split_lines(out2) + _split_lines(err2))
  conf = _parse_nginx_conf_path(merged)
  if conf:
    return conf
  raise HTTPException(status_code=400, detail='未解析到nginx配置路径')


async def _is_nginx_running_on_server(server_row) -> bool:
  """检查指定业务服务器上的 Nginx 是否运行。

  参数：
  - server_row：服务器 ORM 对象。

  返回：
  - True 表示运行中；False 表示未运行。
  """
  if _is_local_server_ip(str(getattr(server_row, 'ip', '') or '').strip()):
    return await _is_nginx_running()
  code, _out, _err = await _run_server_shell(server_row, 'pgrep -x nginx >/dev/null 2>&1', timeout=15)
  return code == 0


async def _get_running_nginx_conf_path_on_server(server_row) -> str:
  """获取指定业务服务器正在运行的 Nginx 主配置文件路径。"""
  if _is_local_server_ip(str(getattr(server_row, 'ip', '') or '').strip()):
    return await _get_running_nginx_conf_path()

  code, out, _err = await _run_server_shell(server_row, "pgrep -a -x nginx | head -n 1", timeout=15)
  if code == 0 and out.strip():
    line = out.strip().splitlines()[0]
    m = re.search(r'\s-c\s+([^\s]+)', line)
    if m:
      return m.group(1).strip()

  _code2, out2, err2 = await _run_server_shell(server_row, 'nginx -V 2>&1', timeout=20)
  merged = '\n'.join(_split_lines(out2) + _split_lines(err2))
  conf = _parse_nginx_conf_path(merged)
  if conf:
    return conf
  raise HTTPException(status_code=400, detail='未解析到nginx配置路径')


def _strip_nginx_comments(text: str) -> str:
  """移除 Nginx 配置中的注释文本。

  参数：
  - text：Nginx 配置文件内容。

  作用：
  - 后续解析 include、server、listen、proxy_pass 时避免被注释内容干扰。
  """
  lines = []
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
  for idx, part in enumerate(parts):
    if part == 'nginx':
      return '/' + '/'.join(parts[:idx + 1])
  return os.path.dirname(normalized)


def _include_pattern_directory(pattern: str) -> str:
  """从 include 规则中提取目录部分。"""
  normalized = os.path.normpath(str(pattern or '').strip())
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

  conf_files: list[dict[str, str]] = []
  new_conf_dirs: list[dict[str, str]] = []
  seen_files: set[str] = set()
  seen_patterns: set[str] = set()
  seen_dirs: set[tuple[str, str]] = set()

  def add_file(path: str, source: str, include_pattern: str = ''):
    """把一个真实存在的 .conf 文件加入可选配置文件列表。"""
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

  base_dir = os.path.dirname(main_conf)
  for item in _extract_nginx_include_patterns_by_scope(text):
    pattern = str(item.get('pattern') or '').strip()
    source = str(item.get('source') or '').strip() or 'include'
    resolved_pattern = _resolve_nginx_include_pattern(pattern, base_dir)
    if not resolved_pattern:
      continue
    add_new_dir(resolved_pattern, source)
    for matched in sorted(glob.glob(resolved_pattern)):
      if os.path.isfile(matched) and matched.endswith('.conf'):
        add_file(matched, source, resolved_pattern)

  return {'conf_files': conf_files, 'new_conf_dirs': new_conf_dirs}


def _collect_nginx_conf_files(main_conf_path: str) -> list[dict[str, str]]:
  """兼容旧调用：只返回已有 Nginx 配置文件列表。"""
  return _collect_nginx_conf_inventory(main_conf_path).get('conf_files', [])


def _build_remote_nginx_inventory_command(main_conf_path: str) -> str:
  """生成远端服务器收集 Nginx 配置清单的 Python 脚本命令。"""
  conf_literal = json.dumps(str(main_conf_path or ''))
  script = r"""
import glob
import json
import os
import re

main_conf = os.path.normpath(__CONF_LITERAL__.strip())

def strip_comments(text):
    lines = []
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

def direct_includes(conf_text):
    clean = strip_comments(conf_text)
    result = []
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

def named_block_inner_ranges(text, block_name):
    ranges = []
    clean = strip_comments(text)
    pattern = re.compile(r'(^|\n)\s*' + re.escape(block_name) + r'\s*\{')
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

def includes_by_scope(conf_text):
    result = []
    for pattern in direct_includes(conf_text):
        result.append({'pattern': pattern, 'source': 'top'})
    clean = strip_comments(conf_text)
    for start, end in named_block_inner_ranges(clean, 'http'):
        for pattern in direct_includes(clean[start:end]):
            result.append({'pattern': pattern, 'source': 'http'})
    return result

def resolve_pattern(pattern, base_dir):
    raw = str(pattern or '').strip().strip('"').strip("'")
    if not raw:
        return ''
    if raw.startswith('/'):
        return raw
    return os.path.normpath(os.path.join(base_dir, raw))

def include_pattern_directory(pattern):
    normalized = os.path.normpath(str(pattern or '').strip())
    positions = [pos for pos in [normalized.find('*'), normalized.find('?'), normalized.find('[')] if pos >= 0]
    if positions:
        prefix = normalized[:min(positions)]
        directory = os.path.dirname(prefix.rstrip('/')) if not prefix.endswith('/') else prefix.rstrip('/')
    else:
        directory = os.path.dirname(normalized)
    return os.path.normpath(directory) if directory else ''

def nginx_base_dir(path):
    normalized = os.path.normpath(str(path or '').strip())
    if not normalized.startswith('/'):
        return ''
    parts = normalized.strip('/').split('/')
    for idx, part in enumerate(parts):
        if part == 'nginx':
            return '/' + '/'.join(parts[:idx + 1])
    return os.path.dirname(normalized)

def is_nginx_modules_path(path):
    normalized = str(path or '').strip().replace('\\\\', '/')
    return normalized == '/usr/share/nginx/modules' or normalized.startswith('/usr/share/nginx/modules/')

conf_files = []
new_conf_dirs = []
seen_files = set()
seen_patterns = set()
seen_dirs = set()

def add_file(path, source, include_pattern=''):
    normalized = os.path.normpath(str(path or '').strip())
    if not normalized or normalized in seen_files or not os.path.isfile(normalized):
        return
    if is_nginx_modules_path(normalized):
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

def add_include_pattern(pattern, source):
    normalized = os.path.normpath(str(pattern or '').strip())
    base_name = os.path.basename(normalized).lower()
    if (not base_name.endswith('.conf')) or is_nginx_modules_path(normalized) or any(token in base_name for token in ['*', '?', '[']):
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

def add_new_dir(resolved_pattern, source):
    if is_nginx_modules_path(resolved_pattern):
        return
    base_name = os.path.basename(str(resolved_pattern or '').strip()).lower()
    if not base_name.endswith('.conf'):
        return
    directory = include_pattern_directory(resolved_pattern)
    if not directory or not os.path.isdir(directory):
        return
    base_dir = nginx_base_dir(directory)
    if not base_dir:
        return
    key = base_dir + '|' + directory
    if key in seen_dirs:
        return
    seen_dirs.add(key)
    rel = os.path.relpath(directory, base_dir)
    folder_name = '' if rel == '.' else rel.split(os.sep)[0]
    label = (folder_name + ' (' + directory + ')') if folder_name else directory
    new_conf_dirs.append({
        'base_dir': base_dir,
        'directory': directory,
        'folder_name': folder_name,
        'include_pattern': resolved_pattern,
        'source': source,
        'label': label,
        'status': 'available',
    })

if main_conf and os.path.isfile(main_conf):
    add_file(main_conf, 'main')
    with open(main_conf, 'r', encoding='utf-8', errors='replace') as f:
        text = f.read()
    base_dir = os.path.dirname(main_conf)
    for item in includes_by_scope(text):
        resolved = resolve_pattern(item.get('pattern', ''), base_dir)
        source = item.get('source') or 'include'
        if not resolved:
            continue
        add_new_dir(resolved, source)
        for matched in sorted(glob.glob(resolved)):
            if os.path.isfile(matched) and matched.endswith('.conf'):
                add_file(matched, source, resolved)

print(json.dumps({'conf_files': conf_files, 'new_conf_dirs': new_conf_dirs}, ensure_ascii=False))
"""
  script = script.replace("__CONF_LITERAL__", conf_literal)
  return "python3 - <<'PY'\n" + script.strip() + "\nPY"


async def _collect_nginx_conf_inventory_on_server(server_row, main_conf_path: str) -> dict[str, list[dict[str, str]]]:
  """收集指定业务服务器上的 Nginx 配置清单。"""
  if _is_local_server_ip(str(getattr(server_row, 'ip', '') or '').strip()):
    return _collect_nginx_conf_inventory(main_conf_path)

  command = _build_remote_nginx_inventory_command(main_conf_path)
  code, out, err = await _run_server_shell(server_row, command, timeout=30)
  if code != 0:
    msg = err.strip() or out.strip() or '读取Nginx配置失败'
    raise HTTPException(status_code=400, detail=msg)

  try:
    line = out.strip().splitlines()[-1] if out.strip() else '{}'
    data = json.loads(line)
  except Exception as ex:
    raise HTTPException(status_code=500, detail=f'解析Nginx配置结果失败：{str(ex)}')

  if not isinstance(data, dict):
    return {'conf_files': [], 'new_conf_dirs': []}
  conf_files = data.get('conf_files') if isinstance(data.get('conf_files'), list) else []
  new_conf_dirs = data.get('new_conf_dirs') if isinstance(data.get('new_conf_dirs'), list) else []
  return {'conf_files': conf_files, 'new_conf_dirs': new_conf_dirs}


def _validate_requested_nginx_conf_path(requested_path: str, inventory: dict[str, list[dict[str, str]]]) -> str:
  """校验前端选择或新建的 Nginx 配置文件路径是否合法。"""
  conf_path = os.path.normpath(str(requested_path or '').strip())
  if not conf_path:
    raise HTTPException(status_code=400, detail='请选择Nginx配置文件路径')
  if not conf_path.startswith('/'):
    raise HTTPException(status_code=400, detail='Nginx配置文件路径必须是绝对路径')

  existing_paths = {
    os.path.normpath(str(item.get('path') or '').strip())
    for item in inventory.get('conf_files', [])
    if str(item.get('path') or '').strip()
  }
  if conf_path in existing_paths:
    return conf_path

  filename = os.path.basename(conf_path)
  if not filename or '/' in filename or '\\' in filename or not filename.lower().endswith('.conf'):
    raise HTTPException(status_code=400, detail='新建Nginx配置文件名必须以 .conf 结尾，且不能包含路径分隔符')

  parent_dir = os.path.dirname(conf_path)
  allowed_dirs = {
    os.path.normpath(str(item.get('directory') or '').strip())
    for item in inventory.get('new_conf_dirs', [])
    if str(item.get('directory') or '').strip()
  }
  if parent_dir not in allowed_dirs:
    raise HTTPException(status_code=400, detail='新建Nginx配置文件必须位于主配置顶层include或http块include允许的目录中')
  return conf_path


def _find_server_block_ranges(text: str) -> list[tuple[int, int]]:
  """查找 Nginx 配置中所有 server 块的字符范围。"""
  ranges: list[tuple[int, int]] = []
  pattern = re.compile(r'(^|\n)\s*server\s*\{')
  for match in pattern.finditer(text):
    start = match.start(0)
    brace_start = text.find('{', match.start(0), match.end(0) + 4)
    if brace_start < 0:
      continue
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
  name = re.escape(project_name)
  return re.search(rf'(?m)^\s*#\s*pspm_project\s+{name}\s*$', block_text) is not None


def _server_block_listen_ports(block_text: str) -> set[int]:
  """提取 server 块中的 listen 端口集合。"""
  ports: set[int] = set()
  for m in re.finditer(r'(?m)^\s*listen\s+([^;]+);', block_text):
    raw = m.group(1)
    hit = re.search(r'(?<!\d)(\d{2,5})(?!\d)', raw)
    if not hit:
      continue
    try:
      p = int(hit.group(1))
    except Exception:
      continue
    if 1 <= p <= 65535:
      ports.add(p)
  return ports


def _server_block_proxy_pass_ports(block_text: str) -> set[int]:
  """提取 server 块中 proxy_pass 指向的端口集合。"""
  ports: set[int] = set()
  clean = _strip_nginx_comments(block_text)
  for m in re.finditer(r'(?m)^\s*proxy_pass\s+([^;]+);', clean):
    raw = m.group(1).strip()
    for hit in re.finditer(r':(\d{2,5})(?:/|$|[^0-9])', raw):
      try:
        p = int(hit.group(1))
      except Exception:
        continue
      if 1 <= p <= 65535:
        ports.add(p)
  return ports


def _read_nginx_conf_text(conf_path: str) -> str:
  """读取本机 Nginx 配置文件文本。"""
  if not conf_path or not os.path.isfile(conf_path):
    return ''
  try:
    with open(conf_path, 'r', encoding='utf-8', errors='replace') as f:
      return f.read()
  except Exception:
    return ''


async def _check_nginx_port_conflict(port: int, conf_path: str, project_name: str = '') -> dict[str, bool | str]:
  """检查本机 Nginx 配置中端口是否冲突。"""
  result: dict[str, bool | str] = {'listen': False, 'proxy_pass': False, 'conf_path': ''}
  candidates = _collect_nginx_conf_files(conf_path)
  if not candidates and conf_path and os.path.isfile(conf_path):
    candidates = [{'path': os.path.normpath(conf_path), 'source': 'main'}]

  for item in candidates:
    path = str(item.get('path') or '').strip()
    if not path or not os.path.isfile(path):
      continue
    conf_text = _read_nginx_conf_text(path)
    if not conf_text:
      continue
    for block_start, block_end in _find_server_block_ranges(conf_text):
      block = conf_text[block_start:block_end]
      if project_name and _server_block_contains_project(block, project_name):
        continue
      if port in _server_block_listen_ports(block):
        result['listen'] = True
        result['conf_path'] = path
      if port in _server_block_proxy_pass_ports(block):
        result['proxy_pass'] = True
        result['conf_path'] = path
      if result['listen'] or result['proxy_pass']:
        return result
  return result


async def _check_nginx_listen_conflict(port: int, conf_path: str, project_name: str = '') -> bool:
  """兼容旧调用：只检查 listen 端口是否冲突。"""
  conflict = await _check_nginx_port_conflict(port, conf_path, project_name=project_name)
  return bool(conflict.get('listen'))


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
  safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', project_name)
  safe_username = re.sub(r'[^a-zA-Z0-9_\-]', '_', username or 'root')
  frontend_root = str(frontend_root or '').strip() or f'/home/{safe_username}/frontend_dist/{project_name}'
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
  text = str(block_text or '').strip()
  if not text:
    raise HTTPException(status_code=400, detail='请先确认Nginx详细配置')
  if not _find_server_block_ranges(text):
    raise HTTPException(status_code=400, detail='Nginx详细配置必须包含 server 块')
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
  ranges = _find_server_block_ranges(conf_text)
  for start, end in ranges:
    block = conf_text[start:end]
    if _server_block_contains_project(block, project_name):
      return conf_text[:start] + new_block + conf_text[end:]
  if conf_text and not conf_text.endswith('\n'):
    conf_text += '\n'
  return conf_text + '\n' + new_block


def _remove_project_server_blocks(conf_text: str, project_name: str) -> tuple[str, int]:
  """从 Nginx 配置文本中删除指定项目的 server block。"""
  ranges = _find_server_block_ranges(conf_text)
  if not ranges:
    return conf_text, 0
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


async def _apply_nginx_conf_change(conf_path: str, transform_fn) -> tuple[bool, str]:
  """在本机安全修改 Nginx 配置文件。"""
  conf = str(conf_path or '').strip()
  if not conf.startswith('/'):
    return False, 'nginx配置路径不合法'
  parent_dir = os.path.dirname(conf)
  if not parent_dir or not os.path.isdir(parent_dir):
    return False, f'nginx配置目录不存在：{parent_dir}'
  if not conf.lower().endswith('.conf'):
    return False, 'nginx配置文件必须以 .conf 结尾'

  bak = f'{conf}_bak'
  existed = os.path.isfile(conf)
  if existed:
    with open(conf, 'r', encoding='utf-8', errors='replace') as f:
      old_text = f.read()
  else:
    old_text = ''

  new_text = transform_fn(old_text)

  if os.path.exists(bak):
    os.remove(bak)
  if existed:
    with open(bak, 'w', encoding='utf-8') as f:
      f.write(old_text)

  try:
    with open(conf, 'w', encoding='utf-8') as f:
      f.write(new_text)
  except Exception as ex:
    if existed and os.path.exists(bak):
      if os.path.exists(conf):
        os.remove(conf)
      os.replace(bak, conf)
    elif (not existed) and os.path.exists(conf):
      os.remove(conf)
    return False, f'写入nginx配置失败：{str(ex)}'

  test_code, test_out, test_err = await _run_shell('nginx -t', timeout=20)
  if test_code != 0:
    if os.path.exists(conf):
      os.remove(conf)
    if existed and os.path.exists(bak):
      os.replace(bak, conf)
    msg = (test_err or test_out or 'nginx -t失败').strip()
    return False, f'nginx配置校验失败并已回滚：{msg}'

  reload_code, reload_out, reload_err = await _run_shell('nginx -s reload', timeout=20)
  if reload_code != 0:
    if os.path.exists(conf):
      os.remove(conf)
    if existed and os.path.exists(bak):
      os.replace(bak, conf)
    msg = (reload_err or reload_out or 'nginx reload失败').strip()
    return False, f'nginx重载失败并已回滚：{msg}'

  return True, 'ok'


async def _read_text_on_server(server_row, path: str, timeout: int = 30) -> tuple[bool, str]:
  """读取指定业务服务器上的文本文件。"""
  target = str(path or '').strip()
  if not target.startswith('/'):
    return False, '路径不合法'
  if _is_local_server_ip(str(getattr(server_row, 'ip', '') or '').strip()):
    try:
      if not os.path.isfile(target):
        return True, ''
      with open(target, 'r', encoding='utf-8', errors='replace') as f:
        return True, f.read()
    except Exception as ex:
      return False, f'读取文件失败：{str(ex)}'

  py = 'import pathlib,sys; p=pathlib.Path(sys.argv[1]); sys.stdout.write(p.read_text(encoding="utf-8", errors="replace") if p.is_file() else "")'
  command = 'python3 -c ' + shlex.quote(py) + ' ' + shlex.quote(target)
  code, out, err = await _run_server_shell(server_row, command, timeout=timeout)
  if code != 0:
    return False, (err.strip() or out.strip() or '读取远程文件失败')
  return True, out


async def _check_nginx_port_conflict_on_server(server_row, port: int, conf_path: str, project_name: str = '') -> dict[str, bool | str]:
  """检查指定业务服务器 Nginx 配置中的端口冲突。"""
  if _is_local_server_ip(str(getattr(server_row, 'ip', '') or '').strip()):
    return await _check_nginx_port_conflict(port, conf_path, project_name=project_name)

  result: dict[str, bool | str] = {'listen': False, 'proxy_pass': False, 'conf_path': ''}
  inventory = await _collect_nginx_conf_inventory_on_server(server_row, conf_path)
  candidates = inventory.get('conf_files', []) or []
  if not candidates and conf_path:
    candidates = [{'path': os.path.normpath(conf_path), 'source': 'main'}]

  for item in candidates:
    path = str(item.get('path') or '').strip()
    if not path:
      continue
    ok, conf_text = await _read_text_on_server(server_row, path)
    if not ok or not conf_text:
      continue
    for block_start, block_end in _find_server_block_ranges(conf_text):
      block = conf_text[block_start:block_end]
      if project_name and _server_block_contains_project(block, project_name):
        continue
      if port in _server_block_listen_ports(block):
        result['listen'] = True
        result['conf_path'] = path
      if port in _server_block_proxy_pass_ports(block):
        result['proxy_pass'] = True
        result['conf_path'] = path
      if result['listen'] or result['proxy_pass']:
        return result
  return result


async def _is_port_in_use_on_server(server_row, port: int) -> bool:
  """检查指定业务服务器系统端口是否被监听占用。"""
  if _is_local_server_ip(str(getattr(server_row, 'ip', '') or '').strip()):
    return await _is_port_in_use(port)
  check_cmd = (
    "ss -lntH | awk '{print $4}' "
    "| sed -E 's#.*:([0-9]+)$#\\1#' "
    f"| grep -x {int(port)} >/dev/null 2>&1"
  )
  code, _out, _err = await _run_server_shell(server_row, check_cmd, timeout=15)
  return code == 0


async def _apply_nginx_conf_change_on_server(server_row, conf_path: str, transform_fn) -> tuple[bool, str]:
  """在指定业务服务器安全修改 Nginx 配置文件。"""
  server_ip = str(getattr(server_row, 'ip', '') or '').strip()
  if not server_ip:
    return False, 'Nginx服务器IP不能为空'
  if _is_local_server_ip(server_ip):
    return await _apply_nginx_conf_change(conf_path, transform_fn)

  conf = str(conf_path or '').strip()
  if not conf.startswith('/'):
    return False, 'nginx配置路径不合法'
  if not conf.lower().endswith('.conf'):
    return False, 'nginx配置文件必须以.conf结尾'

  ok, old_text = await _read_text_on_server(server_row, conf, timeout=30)
  if not ok:
    return False, old_text
  try:
    new_text = transform_fn(old_text)
  except Exception as ex:
    return False, f'生成nginx配置失败：{str(ex)}'

  payload = json.dumps({'conf': conf, 'new_text': new_text}, ensure_ascii=False)
  remote_script = """
payload = json.loads(sys.stdin.read())
conf = str(payload.get('conf') or '').strip()
new_text = str(payload.get('new_text') or '')
if not conf.startswith('/'):
    print('nginx配置路径不合法', file=sys.stderr)
    sys.exit(2)
if not conf.lower().endswith('.conf'):
    print('nginx配置文件必须以.conf结尾', file=sys.stderr)
    sys.exit(2)
parent = os.path.dirname(conf)
if not parent or not os.path.isdir(parent):
    print('nginx配置目录不存在：' + parent, file=sys.stderr)
    sys.exit(2)
bak = conf + '_bak'
existed = os.path.isfile(conf)
old_text = pathlib.Path(conf).read_text(encoding='utf-8', errors='replace') if existed else ''
try:
    if os.path.exists(bak):
        os.remove(bak)
    if existed:
        pathlib.Path(bak).write_text(old_text, encoding='utf-8')
    pathlib.Path(conf).write_text(new_text, encoding='utf-8')
except Exception as ex:
    try:
        if existed and os.path.exists(bak):
            if os.path.exists(conf):
                os.remove(conf)
            os.replace(bak, conf)
        elif (not existed) and os.path.exists(conf):
            os.remove(conf)
    finally:
        print('写入nginx配置失败：' + str(ex), file=sys.stderr)
        sys.exit(3)

def rollback():
    if os.path.exists(conf):
        os.remove(conf)
    if existed and os.path.exists(bak):
        os.replace(bak, conf)

result = subprocess.run(['nginx', '-t'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
if result.returncode != 0:
    rollback()
    msg = (result.stderr or result.stdout or 'nginx -t failed').strip()
    print('nginx配置校验失败并已回滚：' + msg, file=sys.stderr)
    sys.exit(4)
result = subprocess.run(['nginx', '-s', 'reload'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
if result.returncode != 0:
    rollback()
    msg = (result.stderr or result.stdout or 'nginx reload failed').strip()
    print('nginx重载失败并已回滚：' + msg, file=sys.stderr)
    sys.exit(5)
print('ok')
""".strip()
  command = 'printf %s ' + shlex.quote(payload) + ' | python3 -c ' + shlex.quote(remote_script)
  code, out, err = await _run_server_shell(server_row, command, timeout=60)
  if code != 0:
    return False, (err.strip() or out.strip() or '远程Nginx配置写入失败')
  return True, 'ok'
