import os
import re
import shlex
import time
from typing import Any

from fastapi import HTTPException

from app.utils.pspm.path_utils import (
  _normalize_path,
  _safe_command,
  _safe_conda_name,
  _safe_optional_port_text,
  _safe_project_shell_script,
)
from app.utils.pspm.project_config import PORT_MAX, PORT_MIN
from app.utils.pspm.shell_utils import _run_server_shell, _split_lines

MSG_NO_START_COMMAND = '\u6682\u65e0\u914d\u7f6e\u542f\u52a8\u547d\u4ee4'
MSG_ENTRY_EMPTY = '\u9879\u76ee\u5165\u53e3\u6587\u4ef6\u4f4d\u7f6e\u4e0d\u80fd\u4e3a\u7a7a'
MSG_ENTRY_INVALID = '\u9879\u76ee\u5165\u53e3\u6587\u4ef6\u4f4d\u7f6e\u4e0d\u5408\u6cd5'
MSG_ENTRY_OUTSIDE = '\u9879\u76ee\u5165\u53e3\u6587\u4ef6\u4f4d\u7f6e\u8d85\u51fa\u9879\u76ee\u76ee\u5f55'
MSG_ENTRY_MISSING = '\u9879\u76ee\u5165\u53e3\u6587\u4ef6\u4e0d\u5b58\u5728'
MSG_RUNNING = '\u8fd0\u884c\u4e2d'
MSG_STOPPED = '\u5df2\u505c\u6b62'
MSG_ALREADY_RUNNING = '\u9879\u76ee\u5df2\u5728\u8fd0\u884c\u4e2d'
MSG_START_FAIL = '\u542f\u52a8\u5931\u8d25'
MSG_PID_EMPTY = '\u65e0\u6cd5\u83b7\u53d6PID'
MSG_PROCESS_DEAD = '\u8fdb\u7a0b\u672a\u5b58\u6d3b'
MSG_START_TIME_FAIL = '\u65e0\u6cd5\u8bfb\u53d6\u8fdb\u7a0b\u542f\u52a8\u65f6\u95f4'
MSG_FRONT = '\u524d\u53f0'
MSG_BACK = '\u540e\u53f0'
MSG_STARTED = '\u5df2{mode}\u542f\u52a8\uff1aPID={pid}'
MSG_PORT = '\u7aef\u53e3'
MSG_UNKNOWN = '\u672a\u77e5'
MSG_STOP_SUCCESS = '\u505c\u6b62\u6210\u529f'
MSG_NOT_RUNNING = '\u672a\u8fd0\u884c'
MSG_STOP_FAIL = '\u505c\u6b62\u5931\u8d25'
MSG_SECURITY_FAIL = '\u5b89\u5168\u6821\u9a8c\u5931\u8d25\uff0c\u62d2\u7edd\u505c\u6b62'
MSG_PID_RECORD_MISSING = '\u672a\u627e\u5230\u8fd0\u884c\u4e2d\u7684PID\u8bb0\u5f55'
MSG_PID_RECORD_EMPTY = 'PID\u8bb0\u5f55\u4e3a\u7a7a\uff0c\u5df2\u6e05\u7406'
MSG_PID_RECORD_INVALID = 'PID\u8bb0\u5f55\u975e\u6cd5\uff0c\u5df2\u6e05\u7406'
MSG_PROCESS_MISSING_CLEANED = '\u8fdb\u7a0b\u4e0d\u5b58\u5728\uff0c\u5df2\u6e05\u7406PID\u8bb0\u5f55'
MSG_PID_META_MISMATCH = '\u5b89\u5168\u6821\u9a8c\u5931\u8d25\uff1aPID\u4e0e\u5143\u6570\u636e\u4e0d\u4e00\u81f4\uff0c\u62d2\u7edd\u505c\u6b62'
MSG_START_TIME_MISMATCH = '\u5b89\u5168\u6821\u9a8c\u5931\u8d25\uff1a\u8fdb\u7a0b\u542f\u52a8\u65f6\u95f4\u4e0d\u4e00\u81f4\uff0c\u62d2\u7edd\u505c\u6b62'
MSG_STILL_RUNNING = '\u505c\u6b62\u5931\u8d25\uff1a\u8fdb\u7a0b\u4ecd\u5728\u8fd0\u884c'
MSG_NO_PROCESS_INFO = '\u672a\u83b7\u53d6\u5230\u8fdb\u7a0b\u4fe1\u606f'
MSG_NO_LISTEN_PORT = '\u672a\u68c0\u6d4b\u5230\u76d1\u542c\u7aef\u53e3'
MSG_CURRENT_RECORD_PORT = '\u5f53\u524d\u8bb0\u5f55\u7aef\u53e3'
MSG_NO_LOG = '\u6682\u65e0\u8fd0\u884c\u65e5\u5fd7\u8f93\u51fa'
MSG_NO_PID = '\u672a\u627e\u5230 PID'
MSG_ENTER_DIR_OK = '\u5df2\u8fdb\u5165\u76ee\u5f55'
MSG_CONDA_OK = 'Conda\u73af\u5883\u5df2\u6fc0\u6d3b'
MSG_SERVICE_STARTING = '\u670d\u52a1\u542f\u52a8\u4e2d'
MSG_SERVICE_CHECK_OK = '\u540e\u7aef\u5df2\u68c0\u6d4b\u8fdb\u7a0b\u548c\u7aef\u53e3'
MSG_FRONT_HINT = '\u524d\u53f0\u542f\u52a8\u7531\u7cfb\u7edf\u4fdd\u6301\u8fd0\u884c\uff0c\u53ef\u5728\u7ec8\u7aef\u4f7f\u7528 Ctrl+C \u505c\u6b62'


async def _detect_remote_conda_init(server_row) -> str:
  candidates = [
    '/root/miniforge3/etc/profile.d/conda.sh',
    '/root/miniconda3/etc/profile.d/conda.sh',
    '/root/anaconda3/etc/profile.d/conda.sh',
    '/opt/miniforge3/etc/profile.d/conda.sh',
    '/opt/miniconda3/etc/profile.d/conda.sh',
    '/opt/anaconda3/etc/profile.d/conda.sh',
  ]
  checks = ' '.join(f'{shlex.quote(path)}' for path in candidates)
  command = (
    'for p in ' + checks + '; do '
    'if [ -f "$p" ]; then echo "$p"; exit 0; fi; '
    'done; '
    'command -v conda >/dev/null 2>&1 && echo "" && exit 0; '
    'exit 1'
  )
  code, out, _err = await _run_server_shell(server_row, command, timeout=15)
  if code != 0:
    raise HTTPException(status_code=400, detail='\u672a\u627e\u5230Conda\u521d\u59cb\u5316\u811a\u672c\uff0c\u65e0\u6cd5\u6fc0\u6d3b\u9879\u76eeConda\u73af\u5883')
  path = (out or '').strip().splitlines()[0].strip() if (out or '').strip() else ''
  return f'source {shlex.quote(path)} >/dev/null 2>&1 || true' if path else 'true'


def _project_runtime_dir(project_id: int) -> str:
  return f'/tmp/pspm/runtime/project_{project_id}'


def _project_pid_file(project_id: int) -> str:
  return f'{_project_runtime_dir(project_id)}/service.pid'


def _project_meta_file(project_id: int) -> str:
  return f'{_project_runtime_dir(project_id)}/service.meta'


def _project_log_file(project_id: int) -> str:
  return f'{_project_runtime_dir(project_id)}/service.log'


def _build_project_runtime_paths(project_id: int) -> tuple[str, str, str]:
  return _project_runtime_dir(project_id), _project_pid_file(project_id), _project_meta_file(project_id)


def _extract_port_from_command(command: str) -> str:
  cmd = str(command or '')
  patterns = [
    r'--port(?:=|\s+)(\d{1,5})',
    r'-port(?:=|\s+)(\d{1,5})',
    r'-p(?:=|\s+)(\d{1,5})',
    r':(\d{1,5})(?!\d)',
  ]
  for pattern in patterns:
    hit = re.search(pattern, cmd, flags=re.IGNORECASE)
    if not hit:
      continue
    port = hit.group(1)
    try:
      value = int(port)
    except Exception:
      continue
    if PORT_MIN <= value <= PORT_MAX:
      return str(value)
  return ''


def _starts_with_token(command: str, token: str) -> bool:
  cmd = str(command or '').lstrip()
  return cmd.startswith(token)


def _apply_configured_port(command: str, configured_port: str) -> str:
  cmd = str(command or '')
  port = str(configured_port or '').strip()
  if not port:
    return cmd

  if re.search(r'--port(?:=|\s+)\d{1,5}', cmd, flags=re.IGNORECASE):
    return re.sub(r'--port(?:=|\s+)\d{1,5}', f'--port {port}', cmd, count=1, flags=re.IGNORECASE)
  if re.search(r'(?<!\w)-port(?:=|\s+)\d{1,5}', cmd):
    return re.sub(r'(?<!\w)-port(?:=|\s+)\d{1,5}', f'-port {port}', cmd, count=1)
  if re.search(r'(?<!\w)-p(?:=|\s+)\d{1,5}', cmd):
    return re.sub(r'(?<!\w)-p(?:=|\s+)\d{1,5}', f'-p {port}', cmd, count=1)

  if _starts_with_token(cmd, 'gunicorn'):
    if re.search(r'--bind(?:=|\s+)[^ \t]+', cmd):
      return re.sub(r'(--bind(?:=|\s+))([^ \t]+)', rf'\g<1>0.0.0.0:{port}', cmd, count=1)
    if re.search(r'(?<!\w)-b(?:=|\s+)[^ \t]+', cmd):
      return re.sub(r'((?<!\w)-b(?:=|\s+))([^ \t]+)', rf'\g<1>0.0.0.0:{port}', cmd, count=1)
    return f'{cmd} --bind 0.0.0.0:{port}'

  if _starts_with_token(cmd, 'uvicorn'):
    return f'{cmd} --port {port}'

  if _starts_with_token(cmd, 'python '):
    return f'{cmd} --port {port}'

  raise HTTPException(
    status_code=400,
    detail='\u5df2\u8bbe\u7f6e\u542f\u52a8\u7aef\u53e3\uff0c\u8bf7\u5728\u542f\u52a8\u547d\u4ee4\u4e2d\u4f7f\u7528 {port}\u3001-port/-p \u6216 --bind \u53c2\u6570',
  )


def _get_raw_start_command(project, mode: str) -> str:
  if mode == 'deploy':
    raw_cmd = str(project.deploy_start_command or '').strip()
  else:
    raw_cmd = str(project.dev_start_command or '').strip()
  if not raw_cmd:
    raise HTTPException(status_code=400, detail=MSG_NO_START_COMMAND)
  return raw_cmd


def _resolve_start_command(project, mode: str) -> tuple[str, str]:
  selected_port = ''
  if mode == 'deploy':
    raw_cmd = _safe_command(_get_raw_start_command(project, mode), '\u90e8\u7f72\u542f\u52a8\u547d\u4ee4')
    selected_port = _safe_optional_port_text(project.backend_deploy_port or '')
    cmd = raw_cmd
    if '{port}' in raw_cmd:
      if not selected_port:
        raise HTTPException(status_code=400, detail='\u90e8\u7f72\u542f\u52a8\u547d\u4ee4\u5305\u542b {port}\uff0c\u8bf7\u5148\u914d\u7f6e\u540e\u7aef\u90e8\u7f72\u7aef\u53e3')
      cmd = raw_cmd.replace('{port}', selected_port)
    elif selected_port:
      cmd = _apply_configured_port(raw_cmd, selected_port)
    return cmd, selected_port

  raw_cmd = _safe_command(_get_raw_start_command(project, mode), '\u5f00\u53d1\u542f\u52a8\u547d\u4ee4')
  selected_port = _safe_optional_port_text(project.backend_dev_port or '')
  cmd = raw_cmd
  if '{port}' in raw_cmd:
    if not selected_port:
      raise HTTPException(status_code=400, detail='\u5f00\u53d1\u542f\u52a8\u547d\u4ee4\u5305\u542b {port}\uff0c\u8bf7\u5148\u914d\u7f6e\u540e\u7aef\u5f00\u53d1\u7aef\u53e3')
    cmd = raw_cmd.replace('{port}', selected_port)
  elif selected_port:
    cmd = _apply_configured_port(raw_cmd, selected_port)
  return cmd, selected_port


def _resolve_entry_file_abs_path(project) -> tuple[str, str]:
  backend_path = _normalize_path(project.backend_path or '')
  entry_value = str(project.entry_file_path or '').strip()
  if not entry_value:
    raise HTTPException(status_code=400, detail=MSG_ENTRY_EMPTY)

  if os.path.isabs(entry_value):
    target = os.path.normpath(entry_value)
    display_path = target
  else:
    entry_file = os.path.normpath(entry_value).replace('\\', '/')
    if entry_file in {'.', ''} or entry_file.startswith('../') or entry_file == '..':
      raise HTTPException(status_code=400, detail=MSG_ENTRY_INVALID)
    target = os.path.normpath(os.path.join(backend_path, entry_file))
    display_path = entry_file

  if not target.startswith(f'{backend_path}/') and target != backend_path:
    raise HTTPException(status_code=400, detail=MSG_ENTRY_OUTSIDE)
  return target, display_path


async def _ensure_remote_file_exists(server_row, abs_path: str, display_path: str) -> None:
  code, _out, _err = await _run_server_shell(server_row, f'test -f {shlex.quote(abs_path)}', timeout=15)
  if code != 0:
    raise HTTPException(status_code=400, detail=f'{MSG_ENTRY_MISSING}\uff1a{display_path}')


async def _ensure_project_runtime_config(server_row, project, mode: str) -> str:
  _get_raw_start_command(project, mode)
  _normalize_path(project.backend_path or '')
  _safe_conda_name(project.conda_env_name or '')
  entry_abs_path, display_path = _resolve_entry_file_abs_path(project)
  await _ensure_remote_file_exists(server_row, entry_abs_path, display_path)
  return entry_abs_path


def _extract_marked_value(output: str, key: str) -> str:
  prefix = f'{key}='
  for line in _split_lines(output):
    if line.startswith(prefix):
      return line[len(prefix):].strip()
  return ''


def _extract_marked_block(output: str, begin_key: str, end_key: str) -> str:
  lines = _split_lines(output)
  collecting = False
  values: list[str] = []
  for line in lines:
    if line.strip() == begin_key:
      collecting = True
      continue
    if line.strip() == end_key:
      break
    if collecting:
      values.append(line)
  return '\n'.join(values).strip()


def _build_start_terminal_steps(
  *,
  work_dir: str,
  conda_name: str,
  command: str,
  visible_command: str,
  pid: str,
  port: str,
  run_in_background: bool,
) -> list[dict[str, str]]:
  steps = [
    {'type': 'command', 'text': f'cd {work_dir}'},
    {'type': 'output', 'text': MSG_ENTER_DIR_OK},
    {'type': 'command', 'text': f'conda activate {conda_name}'},
    {'type': 'output', 'text': MSG_CONDA_OK},
    {'type': 'command', 'text': visible_command},
    {'type': 'output', 'text': MSG_SERVICE_STARTING},
  ]
  if pid or port:
    message = MSG_STARTED.format(mode=(MSG_BACK if run_in_background else MSG_FRONT), pid=(pid or MSG_UNKNOWN))
    if port:
      message = f'{message} {MSG_PORT}={port}'
    steps.append({'type': 'output', 'text': message})
  if run_in_background:
    steps.append({'type': 'output', 'text': MSG_SERVICE_CHECK_OK})
  return steps


def _service_ready_script(pid: str, port: str, log_file: str, wait_seconds: int = 20) -> str:
  safe_pid = shlex.quote(str(pid or ''))
  safe_port = shlex.quote(str(port or ''))
  safe_log = shlex.quote(str(log_file or ''))
  return f"""
pid={safe_pid}
port={safe_port}
log_file={safe_log}
wait_seconds={int(wait_seconds)}
if [ -z "$pid" ] || ! echo "$pid" | grep -Eq '^[0-9]+$'; then
  echo 'PSPM_READY=0'
  echo 'PSPM_REASON=PID为空或不合法'
  exit 20
fi
for _i in $(seq 1 "$wait_seconds"); do
  if ! kill -0 "$pid" 2>/dev/null; then
    echo 'PSPM_READY=0'
    echo 'PSPM_REASON=进程已退出'
    echo 'PSPM_LOG_BEGIN'
    tail -n 120 "$log_file" 2>/dev/null || true
    echo 'PSPM_LOG_END'
    exit 21
  fi
  if [ -n "$port" ] && ss -lntH 2>/dev/null | awk '{{print $4}}' | grep -E "(^|:)$port$" >/dev/null; then
    echo 'PSPM_READY=1'
    echo 'PSPM_REASON=端口已监听'
    echo 'PSPM_LOG_BEGIN'
    tail -n 80 "$log_file" 2>/dev/null || true
    echo 'PSPM_LOG_END'
    exit 0
  fi
  sleep 1
done
echo 'PSPM_READY=0'
echo 'PSPM_REASON=等待服务监听端口超时'
echo 'PSPM_LOG_BEGIN'
tail -n 120 "$log_file" 2>/dev/null || true
echo 'PSPM_LOG_END'
exit 23
"""


async def _check_started_process_ready(
  *,
  server_row,
  project_id: int,
  pid: str,
  port: str,
  log_file: str | None = None,
  wait_seconds: int = 20,
) -> dict[str, Any]:
  log_file = str(log_file or '').strip() or _project_log_file(project_id)
  script = _service_ready_script(pid, port, log_file, wait_seconds)
  code, out, err = await _run_server_shell(server_row, _safe_project_shell_script(script), timeout=wait_seconds + 10)
  ready = _extract_marked_value(out, 'PSPM_READY') == '1'
  reason = _extract_marked_value(out, 'PSPM_REASON') or (err.strip() or out.strip() or '')
  log_output = _extract_marked_block(out, 'PSPM_LOG_BEGIN', 'PSPM_LOG_END')
  return {
    'ready': ready,
    'reason': reason,
    'log_output': log_output,
    'stdout': out,
    'stderr': err,
    'exit_code': code,
    'log_file': log_file,
  }


async def _prepare_project_foreground_start(
  *,
  server_row,
  project,
) -> dict[str, Any]:
  entry_abs_path = await _ensure_project_runtime_config(server_row, project, 'dev')
  work_dir = os.path.dirname(entry_abs_path)
  conda_name = _safe_conda_name(project.conda_env_name or '')
  command, configured_port = _resolve_start_command(project, 'dev')
  runtime_dir, pid_file, meta_file = _build_project_runtime_paths(project.id)
  log_file = _project_log_file(project.id)
  selected_port = configured_port or _extract_port_from_command(command)
  return {
    'project_id': project.id,
    'project_name': project.name,
    'work_dir': work_dir,
    'entry_file_path': entry_abs_path,
    'conda_env_name': conda_name,
    'command': command,
    'visible_command': command,
    'port': selected_port,
    'runtime_dir': runtime_dir,
    'pid_file': pid_file,
    'meta_file': meta_file,
    'log_file': log_file,
    'mode': 'dev',
    'run_in_background': False,
  }


async def _finalize_project_foreground_start(
  *,
  server_row,
  project,
  pid: str,
  port: str,
  log_file: str | None = None,
  wait_seconds: int = 20,
) -> dict[str, Any]:
  runtime_dir, pid_file, meta_file = _build_project_runtime_paths(project.id)
  log_file = str(log_file or '').strip() or _project_log_file(project.id)
  now = int(time.time())
  check = await _check_started_process_ready(
    server_row=server_row,
    project_id=project.id,
    pid=pid,
    port=port,
    log_file=log_file,
    wait_seconds=wait_seconds,
  )
  if not check['ready']:
    cleanup = f"""
rm -f {shlex.quote(pid_file)} {shlex.quote(meta_file)}
if echo {shlex.quote(str(pid or ''))} | grep -Eq '^[0-9]+$' && kill -0 {shlex.quote(str(pid or ''))} 2>/dev/null; then
  kill {shlex.quote(str(pid or ''))} 2>/dev/null || true
fi
"""
    await _run_server_shell(server_row, _safe_project_shell_script(cleanup), timeout=10)
    raise HTTPException(status_code=500, detail=f'前台启动失败：{check["reason"] or "服务未完成启动"}')

  start_time_cmd = f"awk '{{print $22}}' /proc/{shlex.quote(str(pid))}/stat 2>/dev/null || true"
  code, start_time, _err = await _run_server_shell(server_row, start_time_cmd, timeout=10)
  start_time = (start_time or '').strip() if code == 0 else ''
  if not start_time:
    raise HTTPException(status_code=500, detail=f'{MSG_START_FAIL}：{MSG_START_TIME_FAIL}')

  meta_script = f"""
set -euo pipefail
mkdir -p {shlex.quote(runtime_dir)}
printf '%s\n' {shlex.quote(str(pid))} > {shlex.quote(pid_file)}
printf '%s\n' {shlex.quote(f'{pid}|{start_time}|dev|{now}|{port or ""}')} > {shlex.quote(meta_file)}
"""
  meta_code, meta_out, meta_err = await _run_server_shell(server_row, _safe_project_shell_script(meta_script), timeout=10)
  if meta_code != 0:
    raise HTTPException(status_code=500, detail=f'写入运行状态失败：{(meta_err or meta_out or "unknown error").strip()}')

  message = MSG_STARTED.format(mode=MSG_FRONT, pid=(pid or MSG_UNKNOWN))
  if port:
    message = f'{message} {MSG_PORT}={port}'
  return {
    'message': message,
    'pid': pid,
    'port': port,
    'mode': 'dev',
    'run_in_background': False,
    'log_file': log_file,
    'log_output': check.get('log_output') or '',
    'ready_reason': check.get('reason') or '',
  }


def _build_stop_terminal_steps(pid: str, meta_file: str, pid_file: str, output: str) -> list[dict[str, str]]:
  return [
    {'type': 'command', 'text': f'cat {pid_file}'},
    {'type': 'output', 'text': pid or MSG_NO_PID},
    {'type': 'command', 'text': f'cat {meta_file}'},
    {'type': 'command', 'text': f'kill {pid}' if pid else 'kill <PID>'},
    {'type': 'output', 'text': output or MSG_STOP_SUCCESS},
  ]


async def _start_project_process(
  *,
  server_row,
  project,
  mode: str,
  run_in_background: bool,
) -> dict[str, Any]:
  entry_abs_path = await _ensure_project_runtime_config(server_row, project, mode)
  work_dir = os.path.dirname(entry_abs_path)
  conda_name = _safe_conda_name(project.conda_env_name or '')
  command, configured_port = _resolve_start_command(project, mode)
  runtime_dir, pid_file, meta_file = _build_project_runtime_paths(project.id)
  log_file = _project_log_file(project.id)
  now = int(time.time())
  launch_mode_text = MSG_BACK if run_in_background else MSG_FRONT
  selected_port = configured_port or _extract_port_from_command(command)
  conda_init = await _detect_remote_conda_init(server_row)
  visible_command = f'nohup {command} >> {log_file} 2>&1 &' if run_in_background else command
  launch_command = f'nohup {command} >> "$log_file" 2>&1 &' if run_in_background else f'{command} >> "$log_file" 2>&1 &'

  script = f"""
set -euo pipefail
PORT_SELECTED={shlex.quote(selected_port)}
runtime_dir={shlex.quote(runtime_dir)}
pid_file={shlex.quote(pid_file)}
meta_file={shlex.quote(meta_file)}
log_file={shlex.quote(log_file)}
mkdir -p "$runtime_dir"
: > "$log_file"
if [ -f "$pid_file" ]; then
  old_pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [ -n "$old_pid" ] && [ -d "/proc/$old_pid" ]; then
    echo "{MSG_ALREADY_RUNNING}\uff08PID=$old_pid\uff09"
    exit 11
  fi
  rm -f "$pid_file"
fi
cd {shlex.quote(work_dir)}
{conda_init}
conda activate {shlex.quote(conda_name)}
{launch_command}
new_pid="$!"
if [ -z "$new_pid" ]; then
  echo "{MSG_START_FAIL}\uff1a{MSG_PID_EMPTY}"
  exit 12
fi
sleep 2
if ! kill -0 "$new_pid" 2>/dev/null; then
  echo "{MSG_START_FAIL}\uff1a{MSG_PROCESS_DEAD}"
  echo "PSPM_LOG_BEGIN"
  tail -n 80 "$log_file" 2>/dev/null || true
  echo "PSPM_LOG_END"
  exit 13
fi
start_time="$(awk '{{print $22}}' /proc/$new_pid/stat 2>/dev/null || true)"
if [ -z "$start_time" ]; then
  echo "{MSG_START_FAIL}\uff1a{MSG_START_TIME_FAIL}"
  exit 14
fi
if [ -z "$PORT_SELECTED" ]; then
  PORT_SELECTED="$(ss -lntpH 2>/dev/null | awk '{{print $4}}' | sed -E 's#.*:([0-9]+)$#\\1#' | grep -E '^[0-9]+$' | head -n 1 || true)"
fi
if [ -n "$PORT_SELECTED" ]; then
  for _i in 1 2 3 4 5; do
    if ss -lntH 2>/dev/null | awk '{{print $4}}' | grep -E "(^|:)$PORT_SELECTED$" >/dev/null; then
      break
    fi
    sleep 1
  done
fi
echo "$new_pid" > "$pid_file"
echo "$new_pid|$start_time|{mode}|{now}|$PORT_SELECTED" > "$meta_file"
echo "PSPM_PID=$new_pid"
echo "PSPM_PORT=$PORT_SELECTED"
echo "PSPM_LOG=$log_file"
echo "PSPM_LOG_BEGIN"
tail -n 20 "$log_file" 2>/dev/null || true
echo "PSPM_LOG_END"
echo "{MSG_STARTED.format(mode=launch_mode_text, pid='$new_pid')}"
"""

  code, out, err = await _run_server_shell(server_row, _safe_project_shell_script(script), timeout=60)
  if code == 11:
    raise HTTPException(status_code=400, detail=(out or err or MSG_ALREADY_RUNNING).strip())
  if code != 0:
    detail = (err or out or 'unknown error').strip()
    raise HTTPException(status_code=500, detail=f'{launch_mode_text}{MSG_START_FAIL}\uff1a{detail}')

  pid = _extract_marked_value(out, 'PSPM_PID')
  actual_port = _extract_marked_value(out, 'PSPM_PORT') or selected_port
  log_output = _extract_marked_block(out, 'PSPM_LOG_BEGIN', 'PSPM_LOG_END')

  message = MSG_STARTED.format(mode=launch_mode_text, pid=(pid or MSG_UNKNOWN))
  if actual_port:
    message = f'{message} {MSG_PORT}={actual_port}'

  return {
    'message': message,
    'pid': pid,
    'port': actual_port,
    'mode': mode,
    'run_in_background': run_in_background,
    'work_dir': work_dir,
    'entry_file_path': entry_abs_path,
    'conda_env_name': conda_name,
    'command': command,
    'visible_command': visible_command,
    'log_file': log_file,
    'stdout': out,
    'stderr': err,
    'exit_code': code,
    'log_output': log_output,
    'terminal_steps': _build_start_terminal_steps(
      work_dir=work_dir,
      conda_name=conda_name,
      command=command,
      visible_command=visible_command,
      pid=pid,
      port=actual_port,
      run_in_background=run_in_background,
    ),
  }


async def _stop_project_process(server_row, project) -> dict[str, Any]:
  _runtime_dir, pid_file, meta_file = _build_project_runtime_paths(project.id)
  script = f"""
set -euo pipefail
pid_file={shlex.quote(pid_file)}
meta_file={shlex.quote(meta_file)}
if [ ! -f "$pid_file" ]; then
  echo "{MSG_PID_RECORD_MISSING}"
  exit 21
fi
pid="$(cat "$pid_file" 2>/dev/null || true)"
meta="$(cat "$meta_file" 2>/dev/null || true)"
echo "PSPM_PID=$pid"
if [ -z "$pid" ]; then
  rm -f "$pid_file" "$meta_file"
  echo "{MSG_PID_RECORD_EMPTY}"
  exit 22
fi
if ! echo "$pid" | grep -Eq '^[0-9]+$'; then
  rm -f "$pid_file" "$meta_file"
  echo "{MSG_PID_RECORD_INVALID}"
  exit 23
fi
if [ ! -d "/proc/$pid" ]; then
  rm -f "$pid_file" "$meta_file"
  echo "{MSG_PROCESS_MISSING_CLEANED}"
  exit 24
fi
meta_pid="$(echo "$meta" | awk -F'|' '{{print $1}}')"
meta_start="$(echo "$meta" | awk -F'|' '{{print $2}}')"
if [ "$meta_pid" != "$pid" ]; then
  echo "{MSG_PID_META_MISMATCH}"
  exit 25
fi
current_start="$(awk '{{print $22}}' /proc/$pid/stat 2>/dev/null || true)"
if [ -n "$meta_start" ] && [ "$current_start" != "$meta_start" ]; then
  echo "{MSG_START_TIME_MISMATCH}"
  exit 26
fi
kill "$pid"
for _i in 1 2 3 4 5 6 7 8 9 10; do
  if [ ! -d "/proc/$pid" ]; then
    break
  fi
  sleep 0.3
done
if [ -d "/proc/$pid" ]; then
  kill -9 "$pid" || true
fi
if [ -d "/proc/$pid" ]; then
  echo "{MSG_STILL_RUNNING}\uff08PID=$pid\uff09"
  exit 27
fi
rm -f "$pid_file" "$meta_file"
echo "{MSG_STOPPED}\uff1aPID=$pid"
"""
  code, out, err = await _run_server_shell(server_row, _safe_project_shell_script(script), timeout=30)
  pid = _extract_marked_value(out, 'PSPM_PID')
  if code in {21, 22, 23, 24}:
    message = (out or err or MSG_NOT_RUNNING).strip()
  elif code in {25, 26}:
    raise HTTPException(status_code=400, detail=(out or err or MSG_SECURITY_FAIL).strip())
  elif code != 0:
    raise HTTPException(status_code=500, detail=f'{MSG_STOP_FAIL}\uff1a{(err or out or "unknown error").strip()}')
  else:
    message = (out or MSG_STOP_SUCCESS).strip()

  return {
    'message': message,
    'pid': pid,
    'stdout': out,
    'stderr': err,
    'exit_code': code,
    'terminal_steps': _build_stop_terminal_steps(pid, meta_file, pid_file, message),
  }


async def _inspect_project_runtime(server_row, project) -> dict[str, str]:
  if not server_row:
    return {'service_status': MSG_STOPPED, 'running_port': ''}

  _runtime_dir, pid_file, meta_file = _build_project_runtime_paths(project.id)
  script = f"""
set -euo pipefail
pid_file={shlex.quote(pid_file)}
meta_file={shlex.quote(meta_file)}
if [ ! -f "$pid_file" ]; then
  echo "service_status={MSG_STOPPED}"
  echo "running_port="
  exit 0
fi
pid="$(cat "$pid_file" 2>/dev/null || true)"
meta="$(cat "$meta_file" 2>/dev/null || true)"
if ! echo "$pid" | grep -Eq '^[0-9]+$'; then
  echo "service_status={MSG_STOPPED}"
  echo "running_port="
  exit 0
fi
if [ ! -d "/proc/$pid" ]; then
  echo "service_status={MSG_STOPPED}"
  echo "running_port="
  exit 0
fi
meta_pid="$(echo "$meta" | awk -F'|' '{{print $1}}')"
meta_start="$(echo "$meta" | awk -F'|' '{{print $2}}')"
meta_port="$(echo "$meta" | awk -F'|' '{{print $5}}')"
if [ -n "$meta_pid" ] && [ "$meta_pid" != "$pid" ]; then
  echo "service_status={MSG_STOPPED}"
  echo "running_port="
  exit 0
fi
if [ -n "$meta_start" ]; then
  current_start="$(awk '{{print $22}}' /proc/$pid/stat 2>/dev/null || true)"
  if [ -n "$current_start" ] && [ "$current_start" != "$meta_start" ]; then
    echo "service_status={MSG_STOPPED}"
    echo "running_port="
    exit 0
  fi
fi
port="$meta_port"
if [ -z "$port" ]; then
  port="$(ss -lntpH 2>/dev/null | grep "pid=$pid," | head -n 1 | sed -E 's#.*:([0-9]+)[[:space:]]+.*#\\1#' || true)"
fi
echo "service_status={MSG_RUNNING}"
echo "running_port=$port"
"""
  code, out, _err = await _run_server_shell(server_row, _safe_project_shell_script(script), timeout=10)
  result = {'service_status': MSG_STOPPED, 'running_port': ''}
  if code != 0:
    return result
  for line in _split_lines(out):
    if '=' not in line:
      continue
    key, value = line.split('=', 1)
    key = key.strip()
    if key in result:
      result[key] = value.strip()
  return result
