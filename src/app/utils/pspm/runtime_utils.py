"""运行态工具模块，封装项目启动停止、PID 文件、运行端口和状态检测逻辑。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

import os
import shlex
import time
from typing import Any

from fastapi import HTTPException

from app.utils.pspm.path_utils import (
  _safe_conda_name,
  _safe_project_shell_script,
)
from app.utils.pspm.runtime_helpers import (
  MSG_ALREADY_RUNNING,
  MSG_BACK,
  MSG_FRONT,
  MSG_NOT_RUNNING,
  MSG_NO_PROCESS_INFO,
  MSG_NO_LISTEN_PORT,
  MSG_PID_EMPTY,
  MSG_PID_META_MISMATCH,
  MSG_PID_RECORD_EMPTY,
  MSG_PID_RECORD_INVALID,
  MSG_PID_RECORD_MISSING,
  MSG_PROCESS_DEAD,
  MSG_PROCESS_MISSING_CLEANED,
  MSG_PORT,
  MSG_RUNNING,
  MSG_SECURITY_FAIL,
  MSG_START_FAIL,
  MSG_START_TIME_FAIL,
  MSG_START_TIME_MISMATCH,
  MSG_STARTED,
  MSG_STILL_RUNNING,
  MSG_STOP_FAIL,
  MSG_STOP_SUCCESS,
  MSG_STOPPED,
  MSG_UNKNOWN,
  _build_project_runtime_paths,
  _build_start_terminal_steps,
  _build_stop_terminal_steps,
  _extract_marked_block,
  _extract_marked_value,
  _extract_port_from_command,
  _project_log_file,
  _resolve_start_command,
  _strip_internal_runtime_markers,
)
from app.utils.pspm.runtime_prepare import (
  _ensure_project_runtime_config,
)
from app.utils.pspm.conda_utils import (
  build_conda_context_shell_command,
  detect_conda_context_on_server,
)
from app.utils.pspm.runtime_ready import (
  _check_started_process_ready,
  _inspect_project_runtime,
)
from app.utils.pspm.shell_utils import _run_server_shell, _split_lines



async def _prepare_project_foreground_start(
  *,
  server_row,
  project,
) -> dict[str, Any]:
  """准备前台启动所需的命令、目录、Conda 和运行态路径。

  返回：
  - WebSocket 前台启动流程需要的上下文字典。
  """
  # 先把项目入口文件解析成远端绝对路径，并校验入口文件真实存在。
  # 前台启动的执行目录固定为入口文件所在目录，等价于用户在服务器终端 cd 到该目录后执行启动命令。
  entry_abs_path = await _ensure_project_runtime_config(server_row, project, 'dev')
  work_dir = os.path.dirname(entry_abs_path)
  conda_name = _safe_conda_name(project.conda_env_name or '')
  # 启动命令来自项目配置；如果命令内能解析端口，则用于后续服务状态回显。
  command, configured_port = _resolve_start_command(project, 'dev')
  # 每个项目有独立运行态目录，保存 PID、进程启动时间、模式和端口，避免误杀同端口或旧 PID 进程。
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
  """前台启动命令发出后记录运行状态并校验服务就绪。

  作用：
  - 等待进程和端口就绪。
  - 写入 PID/meta 文件。
  - 失败时清理运行态记录并尝试停止进程。

  返回：
  - 启动结果、PID、端口和日志片段。
  """
  runtime_dir, pid_file, meta_file = _build_project_runtime_paths(project.id)
  log_file = str(log_file or '').strip() or _project_log_file(project.id)
  now = int(time.time())
  # WebSocket 已经把启动命令发送到真实终端，这里只负责二次确认进程和端口是否进入可用状态。
  # 注意：这里不再按历史日志内容判断失败，避免旧错误日志影响本次启动。
  check = await _check_started_process_ready(
    server_row=server_row,
    project_id=project.id,
    pid=pid,
    port=port,
    log_file=log_file,
    wait_seconds=wait_seconds,
  )
  detected_port = str(check.get('port') or port or '').strip()
  if not check['ready']:
    # 启动未就绪时清理 PID/meta，并尝试停止刚启动的进程，避免前端显示运行中但实际不可用。
    cleanup = f"""
rm -f {shlex.quote(pid_file)} {shlex.quote(meta_file)}
if echo {shlex.quote(str(pid or ''))} | grep -Eq '^[0-9]+$' && kill -0 {shlex.quote(str(pid or ''))} 2>/dev/null; then
  kill {shlex.quote(str(pid or ''))} 2>/dev/null || true
fi
"""
    await _run_server_shell(server_row, _safe_project_shell_script(cleanup), timeout=10)
    raise HTTPException(status_code=500, detail=f'前台启动失败：{check["reason"] or "服务未完成启动"}')

  # 记录 /proc/<pid>/stat 第 22 列 start_time。
  # 停止服务时会再次比对该值，防止 PID 被系统复用后误杀无关进程。
  start_time_cmd = f"awk '{{print $22}}' /proc/{shlex.quote(str(pid))}/stat 2>/dev/null || true"
  code, start_time, _err = await _run_server_shell(server_row, start_time_cmd, timeout=10)
  start_time = (start_time or '').strip() if code == 0 else ''
  if not start_time:
    raise HTTPException(status_code=500, detail=f'{MSG_START_FAIL}：{MSG_START_TIME_FAIL}')

  meta_script = f"""
set -euo pipefail
mkdir -p {shlex.quote(runtime_dir)}
printf '%s\n' {shlex.quote(str(pid))} > {shlex.quote(pid_file)}
printf '%s\n' {shlex.quote(f'{pid}|{start_time}|dev|{now}|{detected_port or ""}')} > {shlex.quote(meta_file)}
"""
  meta_code, meta_out, meta_err = await _run_server_shell(server_row, _safe_project_shell_script(meta_script), timeout=10)
  if meta_code != 0:
    raise HTTPException(status_code=500, detail=f'写入运行状态失败：{(meta_err or meta_out or '未知错误').strip()}')

  message = MSG_STARTED.format(mode=MSG_FRONT, pid=(pid or MSG_UNKNOWN))
  if detected_port:
    message = f'{message} {MSG_PORT}={detected_port}'
  return {
    'message': message,
    'pid': pid,
    'port': detected_port,
    'mode': 'dev',
    'run_in_background': False,
    'log_file': log_file,
    'log_output': check.get('log_output') or '',
    'ready_reason': check.get('reason') or '',
  }


async def _start_project_process(
  *,
  server_row,
  project,
  mode: str,
  run_in_background: bool,
) -> dict[str, Any]:
  """执行后台启动或部署启动。

  参数：
  - server_row：项目所在服务器记录。
  - project：项目 ORM 对象。
  - mode：启动模式，dev/deploy。
  - run_in_background：是否后台运行。

  返回：
  - 启动结果、PID、端口、日志文件和终端展示步骤。
  """
  # 后台启动/部署启动与前台启动使用相同的配置校验入口，保证入口文件、Conda 和命令逻辑一致。
  entry_abs_path = await _ensure_project_runtime_config(server_row, project, mode)
  work_dir = os.path.dirname(entry_abs_path)
  conda_name = _safe_conda_name(project.conda_env_name or '')
  command, configured_port = _resolve_start_command(project, mode)
  runtime_dir, pid_file, meta_file = _build_project_runtime_paths(project.id)
  log_file = _project_log_file(project.id)
  now = int(time.time())
  launch_mode_text = MSG_BACK if run_in_background else MSG_FRONT
  selected_port = configured_port or _extract_port_from_command(command)
  conda_context = await detect_conda_context_on_server(server_row)
  # 按用户要求，后台/部署启动终端展示 nohup 命令；真实执行时丢弃 stdout/stderr，避免 SSH 等待后台进程输出导致接口卡住。
  visible_command = f'nohup {command} &'
  launch_command = f'nohup {command} >/dev/null 2>&1 &'
  launch_script = f'''
cd {shlex.quote(work_dir)}
{conda_context.init_command}
conda activate {shlex.quote(conda_name)}
{launch_command}
echo "PSPM_LAUNCH_PID=$!"
'''
  launch_shell = build_conda_context_shell_command(conda_context, launch_script)

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
    echo "{MSG_ALREADY_RUNNING}（PID=$old_pid）"
    exit 11
  fi
  rm -f "$pid_file"
fi
set +e
launch_output="$({launch_shell} 2>&1)"
launch_status="$?"
set -e
echo "$launch_output"
if [ "$launch_status" -ne 0 ]; then
  echo "{MSG_START_FAIL}：$launch_output"
  exit 12
fi
new_pid="$(echo "$launch_output" | awk -F= '/^PSPM_LAUNCH_PID=/ {{print $2; exit}}')"
if [ -z "$new_pid" ]; then
  echo "{MSG_START_FAIL}：{MSG_PID_EMPTY}"
  exit 12
fi
launch_pid="$new_pid"
work_dir_resolved="$(readlink -f {shlex.quote(work_dir)} 2>/dev/null || printf '%s\n' {shlex.quote(work_dir)})"
_detect_listen_pid_by_port() {{
  [ -n "$PORT_SELECTED" ] || return 0
  ss -lntpH 2>/dev/null | awk -v p="$PORT_SELECTED" '$4 ~ ":"p"$" {{print}}' | while IFS= read -r item; do
    for item_pid in $(echo "$item" | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u); do
      [ -n "$item_pid" ] || continue
      [ -d "/proc/$item_pid" ] || continue
      item_cwd="$(readlink -f "/proc/$item_pid/cwd" 2>/dev/null || true)"
      case "$item_cwd" in
        "$work_dir_resolved"|"$work_dir_resolved"/*)
          printf '%s\n' "$item_pid"
          exit 0
          ;;
      esac
    done
  done | head -n 1
}}
_detect_listen_pair_by_cwd() {{
  ss -lntpH 2>/dev/null | while IFS= read -r item; do
    item_port="$(echo "$item" | awk '{{print $4}}' | awk -F: '{{print $NF}}' | grep -E '^[0-9]+$' | head -n 1)"
    for item_pid in $(echo "$item" | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u); do
      [ -n "$item_pid" ] || continue
      [ -d "/proc/$item_pid" ] || continue
      item_cwd="$(readlink -f "/proc/$item_pid/cwd" 2>/dev/null || true)"
      case "$item_cwd" in
        "$work_dir_resolved"|"$work_dir_resolved"/*)
          printf '%s|%s\n' "$item_pid" "$item_port"
          exit 0
          ;;
      esac
    done
  done | head -n 1
}}
_detected_pid=""
_detected_port=""
for _i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  if [ -n "$PORT_SELECTED" ]; then
    _detected_pid="$(_detect_listen_pid_by_port || true)"
    if [ -n "$_detected_pid" ]; then
      _detected_port="$PORT_SELECTED"
      break
    fi
  fi
  if [ -z "$_detected_pid" ]; then
    _pair="$(_detect_listen_pair_by_cwd || true)"
    if [ -n "$_pair" ]; then
      _detected_pid="$(echo "$_pair" | awk -F'|' '{{print $1}}')"
      _detected_port="$(echo "$_pair" | awk -F'|' '{{print $2}}')"
      break
    fi
  fi
  if ! kill -0 "$launch_pid" 2>/dev/null && [ -z "$PORT_SELECTED" ]; then
    _pair="$(_detect_listen_pair_by_cwd || true)"
    if [ -n "$_pair" ]; then
      _detected_pid="$(echo "$_pair" | awk -F'|' '{{print $1}}')"
      _detected_port="$(echo "$_pair" | awk -F'|' '{{print $2}}')"
    fi
    break
  fi
  sleep 1
done
if [ -n "$_detected_pid" ]; then
  new_pid="$_detected_pid"
  if [ -z "$PORT_SELECTED" ] && [ -n "$_detected_port" ]; then
    PORT_SELECTED="$_detected_port"
  fi
elif [ -n "$PORT_SELECTED" ]; then
  echo "{MSG_START_FAIL}：端口未监听"
  echo "PSPM_LOG_BEGIN"
  tail -n 80 "$log_file" 2>/dev/null || true
  echo "PSPM_LOG_END"
  exit 13
elif kill -0 "$launch_pid" 2>/dev/null; then
  echo "{MSG_START_FAIL}：{MSG_NO_LISTEN_PORT}"
  echo "PSPM_LOG_BEGIN"
  tail -n 80 "$log_file" 2>/dev/null || true
  echo "PSPM_LOG_END"
  exit 13
else
  echo "{MSG_START_FAIL}：{MSG_PROCESS_DEAD}"
  echo "PSPM_LOG_BEGIN"
  tail -n 80 "$log_file" 2>/dev/null || true
  echo "PSPM_LOG_END"
  exit 13
fi
if ! kill -0 "$new_pid" 2>/dev/null; then
  echo "{MSG_START_FAIL}：{MSG_PROCESS_DEAD}"
  echo "PSPM_LOG_BEGIN"
  tail -n 80 "$log_file" 2>/dev/null || true
  echo "PSPM_LOG_END"
  exit 13
fi
start_time="$(awk '{{print $22}}' /proc/$new_pid/stat 2>/dev/null || true)"
if [ -z "$start_time" ]; then
  echo "{MSG_START_FAIL}：{MSG_START_TIME_FAIL}"
  exit 14
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

  # 该脚本一次性完成 cd、conda activate、nohup 启动和 PID/meta 写入。
  # WebSocket 前台启动走真实交互会话，后台/部署启动走这里的非交互脚本。
  code, out, err = await _run_server_shell(server_row, _safe_project_shell_script(script), timeout=60)
  if code == 11:
    raise HTTPException(status_code=400, detail=(out or err or MSG_ALREADY_RUNNING).strip())
  if code != 0:
    detail = _strip_internal_runtime_markers(err or out or '未知错误').strip() or '未知错误'
    raise HTTPException(status_code=500, detail=f'{launch_mode_text}{MSG_START_FAIL}：{detail}')

  # PSPM_* 标记只供后端解析，返回给前端前会构造成更友好的终端步骤和提示语。
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
  """停止项目运行进程并清理 PID/meta 文件。

  参数：
  - server_row：项目所在服务器记录。
  - project：项目 ORM 对象。

  返回：
  - 停止结果、PID、终端展示步骤等信息。
  """
  # 停止服务优先使用 runtime 目录记录的 PID/meta，同时兼容 python main.py 这类父子进程或 zombie 进程场景。
  # 如果记录 PID 已变成 zombie，或真实监听端口的是另一个 PID，则通过 meta 里的端口反查真实监听 PID 并停止。
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
meta_pid="$(echo "$meta" | awk -F'|' '{{print $1}}')"
meta_start="$(echo "$meta" | awk -F'|' '{{print $2}}')"
meta_port="$(echo "$meta" | awk -F'|' '{{print $5}}')"
if [ -n "$meta_pid" ] && [ "$meta_pid" != "$pid" ]; then
  echo "{MSG_PID_META_MISMATCH}"
  exit 25
fi
if ! echo "$meta_port" | grep -Eq '^[0-9]+$'; then
  meta_port=""
fi
pid_alive=0
pid_zombie=0
if [ -d "/proc/$pid" ]; then
  pid_alive=1
  state="$(awk '/^State:/ {{print $2}}' /proc/$pid/status 2>/dev/null || true)"
  if [ "$state" = "Z" ]; then
    pid_zombie=1
  fi
fi
if [ "$pid_alive" = "1" ] && [ "$pid_zombie" = "0" ] && [ -n "$meta_start" ]; then
  current_start="$(awk '{{print $22}}' /proc/$pid/stat 2>/dev/null || true)"
  if [ -n "$current_start" ] && [ "$current_start" != "$meta_start" ]; then
    echo "{MSG_START_TIME_MISMATCH}"
    exit 26
  fi
fi
listen_pid=""
if [ -n "$meta_port" ]; then
  listen_pid="$(ss -lntpH 2>/dev/null | awk -v p="$meta_port" '$4 ~ ":"p"$" {{print}}' | grep -o 'pid=[0-9]*' | head -n 1 | cut -d= -f2 || true)"
fi
targets=""
if [ "$pid_alive" = "1" ] && [ "$pid_zombie" = "0" ]; then
  targets="$targets $pid"
fi
if [ -n "$listen_pid" ] && [ "$listen_pid" != "$pid" ]; then
  targets="$targets $listen_pid"
fi
if [ -z "$(echo "$targets" | xargs 2>/dev/null || true)" ]; then
  if [ -n "$meta_port" ] && ss -lntH 2>/dev/null | awk '{{print $4}}' | grep -E "(^|:)$meta_port$" >/dev/null; then
    echo "{MSG_NO_PROCESS_INFO}，端口仍在监听：$meta_port"
    exit 27
  fi
  rm -f "$pid_file" "$meta_file"
  echo "{MSG_PROCESS_MISSING_CLEANED}"
  exit 24
fi
for target in $targets; do
  if echo "$target" | grep -Eq '^[0-9]+$' && [ -d "/proc/$target" ]; then
    kill "$target" 2>/dev/null || true
  fi
done
for _i in $(seq 1 30); do
  if [ -n "$meta_port" ]; then
    current_listen_pid="$(ss -lntpH 2>/dev/null | awk -v p="$meta_port" '$4 ~ ":"p"$" {{print}}' | grep -o 'pid=[0-9]*' | head -n 1 | cut -d= -f2 || true)"
    if [ -z "$current_listen_pid" ]; then
      break
    fi
    if echo "$current_listen_pid" | grep -Eq '^[0-9]+$' && [ -d "/proc/$current_listen_pid" ]; then
      kill "$current_listen_pid" 2>/dev/null || true
    fi
  else
    still_alive=0
    for target in $targets; do
      if echo "$target" | grep -Eq '^[0-9]+$' && [ -d "/proc/$target" ]; then
        state="$(awk '/^State:/ {{print $2}}' /proc/$target/status 2>/dev/null || true)"
        [ "$state" = "Z" ] || still_alive=1
      fi
    done
    if [ "$still_alive" = "0" ]; then
      break
    fi
  fi
  sleep 0.5
done
if [ -n "$meta_port" ]; then
  current_listen_pid="$(ss -lntpH 2>/dev/null | awk -v p="$meta_port" '$4 ~ ":"p"$" {{print}}' | grep -o 'pid=[0-9]*' | head -n 1 | cut -d= -f2 || true)"
  if echo "$current_listen_pid" | grep -Eq '^[0-9]+$' && [ -d "/proc/$current_listen_pid" ]; then
    kill -9 "$current_listen_pid" 2>/dev/null || true
  fi
  for _i in $(seq 1 20); do
    if ! ss -lntH 2>/dev/null | awk '{{print $4}}' | grep -E "(^|:)$meta_port$" >/dev/null; then
      break
    fi
    sleep 0.5
  done
else
  for target in $targets; do
    if echo "$target" | grep -Eq '^[0-9]+$' && [ -d "/proc/$target" ]; then
      state="$(awk '/^State:/ {{print $2}}' /proc/$target/status 2>/dev/null || true)"
      [ "$state" = "Z" ] || kill -9 "$target" 2>/dev/null || true
    fi
  done
fi
if [ -n "$meta_port" ] && ss -lntH 2>/dev/null | awk '{{print $4}}' | grep -E "(^|:)$meta_port$" >/dev/null; then
  echo "{MSG_STILL_RUNNING}（端口=$meta_port）"
  exit 27
fi
rm -f "$pid_file" "$meta_file"
if [ -n "$listen_pid" ] && [ "$listen_pid" != "$pid" ]; then
  echo "{MSG_STOPPED}：PID=$pid，监听PID=$listen_pid"
else
  echo "{MSG_STOPPED}：PID=$pid"
fi
"""
  # 停止脚本会输出 PSPM_PID 供前端日志展示，同时内部会清理 pid/meta。
  code, out, err = await _run_server_shell(server_row, _safe_project_shell_script(script), timeout=30)
  pid = _extract_marked_value(out, 'PSPM_PID')
  if code in {21, 22, 23, 24}:
    message = _strip_internal_runtime_markers(out or err or MSG_NOT_RUNNING)
  elif code in {25, 26}:
    raise HTTPException(status_code=400, detail=_strip_internal_runtime_markers(out or err or MSG_SECURITY_FAIL))
  elif code != 0:
    raise HTTPException(status_code=500, detail=f'{MSG_STOP_FAIL}：{_strip_internal_runtime_markers(err or out or '未知错误')}')
  else:
    message = _strip_internal_runtime_markers(out or MSG_STOP_SUCCESS)

  return {
    'message': message,
    'pid': pid,
    'stdout': out,
    'stderr': err,
    'exit_code': code,
    'terminal_steps': _build_stop_terminal_steps(pid, meta_file, pid_file, message),
  }
