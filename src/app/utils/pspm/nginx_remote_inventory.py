"""远程 Nginx 配置清单脚本模块。

用途：
- 生成在远程服务器执行的 Python 脚本，用于发现主配置 include 展开的 .conf 文件和允许新建配置的目录。
- 独立维护长 heredoc 脚本，避免 nginx_utils.py 同时承载本地逻辑、远程脚本和写入逻辑。
"""

from __future__ import annotations

import json


def _build_remote_nginx_inventory_command(main_conf_path: str) -> str:
  """生成远端服务器收集 Nginx 配置清单的 Python 脚本命令。"""
  conf_literal = json.dumps(str(main_conf_path or ''))
  script = r"""
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


