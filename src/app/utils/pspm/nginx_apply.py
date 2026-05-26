"""Nginx 配置写入和回滚工具模块。

用途：
- 集中维护本机/远程 Nginx 配置文件写入、nginx -t 校验、reload 和失败回滚逻辑。
- nginx_utils.py 只保留发现、读取、端口冲突检测等轻量封装。
"""

from __future__ import annotations

import json
import os
import shlex

from app.utils.pspm.shell_utils import _is_local_server_ip, _run_server_shell, _run_shell


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
    msg = (result.stderr or result.stdout or 'nginx配置校验失败').strip()
    print('nginx配置校验失败并已回滚：' + msg, file=sys.stderr)
    sys.exit(4)
result = subprocess.run(['nginx', '-s', 'reload'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
if result.returncode != 0:
    rollback()
    msg = (result.stderr or result.stdout or 'nginx重载失败').strip()
    print('nginx重载失败并已回滚：' + msg, file=sys.stderr)
    sys.exit(5)
print('ok')
""".strip()
  command = 'printf %s ' + shlex.quote(payload) + ' | python3 -c ' + shlex.quote(remote_script)
  code, out, err = await _run_server_shell(server_row, command, timeout=60)
  if code != 0:
    return False, (err.strip() or out.strip() or '远程Nginx配置写入失败')
  return True, 'ok'
