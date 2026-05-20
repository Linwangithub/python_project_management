import os
import re
import shlex
import time

from fastapi import HTTPException

from app.utils.pspm.path_utils import (
  _normalize_path,
  _safe_command,
  _safe_conda_name,
  _safe_entry_file_path,
  _safe_optional_port_text,
  _safe_project_shell_script,
)
from app.utils.pspm.project_config import CONDA_INIT, PORT_MAX, PORT_MIN
from app.utils.pspm.shell_utils import _run_shell, _split_lines


def _project_runtime_dir(project_id: int) -> str:
  """返回项目运行时元数据目录。

  参数：
  - project_id：项目 ID。

  作用：
  - 保存当前项目 PID、启动元数据和日志路径。

  返回：
  - `/tmp/pspm/runtime/project_{project_id}`。
  """
  return f'/tmp/pspm/runtime/project_{project_id}'


def _project_pid_file(project_id: int) -> str:
  """返回项目 PID 文件路径。"""
  return f'{_project_runtime_dir(project_id)}/service.pid'


def _project_meta_file(project_id: int) -> str:
  """返回项目启动元数据文件路径。"""
  return f'{_project_runtime_dir(project_id)}/service.meta'


def _project_log_file(project_id: int) -> str:
  """返回项目运行日志文件路径。"""
  return f'{_project_runtime_dir(project_id)}/service.log'


def _build_project_runtime_paths(project_id: int) -> tuple[str, str, str]:
  """一次性构造运行时目录、PID 文件、元数据文件。

  参数：
  - project_id：项目 ID。

  返回：
  - `(runtime_dir, pid_file, meta_file)`。
  """
  return _project_runtime_dir(project_id), _project_pid_file(project_id), _project_meta_file(project_id)


def _extract_port_from_command(command: str) -> str:
  """从启动命令中尝试解析端口。

  参数：
  - command：最终启动命令。

  作用：
  - 如果用户没有在设置中显式填写端口，则启动后尝试从命令中识别实际端口。

  返回：
  - 成功返回端口字符串；失败返回空字符串。
  """
  cmd = str(command or '')
  patterns = [
    r'--port(?:=|\s+)(\d{1,5})',
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
  """判断命令是否以某个命令 token 开头。"""
  cmd = str(command or '').lstrip()
  return cmd.startswith(token)


def _apply_configured_port(command: str, configured_port: str) -> str:
  """把用户设置的端口应用到启动命令。

  参数：
  - command：用户填写的启动命令。
  - configured_port：用户设置的端口。

  作用：
  - 优先替换常见 `--port`、`-port`、`-p`、`--bind`、`-b` 参数。
  - 对 gunicorn、uvicorn、python 命令做常见补全。
  - 对未知命令拒绝自动拼接，避免错误注入导致启动失败。

  返回：
  - 应用端口后的启动命令。
  """
  cmd = str(command or '')
  port = str(configured_port or '').strip()
  if not port:
    return cmd

  # 优先替换通用 --port / -p 参数。
  if re.search(r'--port(?:=|\s+)\d{1,5}', cmd, flags=re.IGNORECASE):
    return re.sub(r'--port(?:=|\s+)\d{1,5}', f'--port {port}', cmd, count=1, flags=re.IGNORECASE)
  if re.search(r'(?<!\w)-port(?:=|\s+)\d{1,5}', cmd):
    return re.sub(r'(?<!\w)-port(?:=|\s+)\d{1,5}', f'-port {port}', cmd, count=1)
  if re.search(r'(?<!\w)-p(?:=|\s+)\d{1,5}', cmd):
    return re.sub(r'(?<!\w)-p(?:=|\s+)\d{1,5}', f'-p {port}', cmd, count=1)

  # gunicorn 常用 --bind / -b 指定监听地址。
  if _starts_with_token(cmd, 'gunicorn'):
    if re.search(r'--bind(?:=|\s+)[^ \t]+', cmd):
      return re.sub(r'(--bind(?:=|\s+))([^ \t]+)', rf'\g<1>0.0.0.0:{port}', cmd, count=1)
    if re.search(r'(?<!\w)-b(?:=|\s+)[^ \t]+', cmd):
      return re.sub(r'((?<!\w)-b(?:=|\s+))([^ \t]+)', rf'\g<1>0.0.0.0:{port}', cmd, count=1)
    return f'{cmd} --bind 0.0.0.0:{port}'

  # uvicorn 常用 --port 指定端口。
  if _starts_with_token(cmd, 'uvicorn'):
    return f'{cmd} --port {port}'

  # python 启动时约定追加 --port。
  if _starts_with_token(cmd, 'python '):
    return f'{cmd} --port {port}'

  raise HTTPException(
    status_code=400,
    detail='已设置启动端口，请在启动命令中使用 {port}、-port/-p 或 --bind 参数',
  )


def _resolve_start_command(project, mode: str) -> tuple[str, str]:
  """解析项目最终启动命令。

  参数：
  - project：项目 ORM 对象，提供启动命令和端口配置。
  - mode：`dev` 使用开发启动命令，`deploy` 使用部署启动命令。

  作用：
  - 如果命令中包含 `{port}`，则替换为设置端口。
  - 如果命令没有占位符但项目设置了端口，则尝试应用端口参数。

  返回：
  - `(command, selected_port)`。
  """
  selected_port = ''
  if mode == 'deploy':
    raw_cmd = _safe_command(project.deploy_start_command or '', '部署启动命令')
    selected_port = _safe_optional_port_text(project.backend_deploy_port or '')
    cmd = raw_cmd
    if '{port}' in raw_cmd:
      if not selected_port:
        raise HTTPException(status_code=400, detail='部署启动命令包含 {port}，请先配置后端部署端口')
      cmd = raw_cmd.replace('{port}', selected_port)
    elif selected_port:
      cmd = _apply_configured_port(raw_cmd, selected_port)
    return cmd, selected_port

  raw_cmd = _safe_command(project.dev_start_command or '', '开发启动命令')
  selected_port = _safe_optional_port_text(project.backend_dev_port or '')
  cmd = raw_cmd
  if '{port}' in raw_cmd:
    if not selected_port:
      raise HTTPException(status_code=400, detail='开发启动命令包含 {port}，请先配置后端开发端口')
    cmd = raw_cmd.replace('{port}', selected_port)
  elif selected_port:
    cmd = _apply_configured_port(raw_cmd, selected_port)
  return cmd, selected_port


def _assert_entry_file_exists(project):
  """确认项目入口文件真实存在且没有越界。

  参数：
  - project：项目 ORM 对象，提供 `backend_path` 和 `entry_file_path`。

  作用：
  - 启动服务前必须确认入口文件存在，否则启动命令容易产生误导性错误。
  """
  backend_path = _normalize_path(project.backend_path or '')
  entry_file = _safe_entry_file_path(project.entry_file_path or '')
  target = os.path.normpath(os.path.join(backend_path, entry_file))
  if not target.startswith(f'{backend_path}/') and target != backend_path:
    raise HTTPException(status_code=400, detail='项目入口文件位置超出项目目录')
  if not os.path.isfile(target):
    raise HTTPException(status_code=400, detail=f'项目入口文件不存在：{entry_file}')


def _ensure_project_runtime_config(project):
  """启动前校验项目运行配置完整性。

  参数：
  - project：项目 ORM 对象。

  作用：
  - 统一校验项目路径、Conda 环境、入口文件、开发启动命令和部署启动命令。
  """
  _normalize_path(project.backend_path or '')
  _safe_conda_name(project.conda_env_name or '')
  _assert_entry_file_exists(project)
  _safe_command(project.dev_start_command or '', '开发启动命令')
  _safe_command(project.deploy_start_command or '', '部署启动命令')


async def _start_project_process(
  *,
  project,
  mode: str,
  run_in_background: bool,
) -> str:
  """启动项目进程。

  参数：
  - project：项目 ORM 对象。
  - mode：`dev` 或 `deploy`。
  - run_in_background：是否后台启动。

  作用：
  - 激活 Conda 环境。
  - 执行最终启动命令。
  - 记录 PID、进程启动时间、模式、端口等元数据。

  返回：
  - 启动成功文案，可能包含 PID 和端口。
  """
  _ensure_project_runtime_config(project)
  backend_path = _normalize_path(project.backend_path or '')
  conda_name = _safe_conda_name(project.conda_env_name or '')
  command, configured_port = _resolve_start_command(project, mode)
  runtime_dir, pid_file, meta_file = _build_project_runtime_paths(project.id)
  log_file = _project_log_file(project.id)
  now = int(time.time())
  launch_mode_text = '后台' if run_in_background else '前台'

  script = f"""
set -euo pipefail
mkdir -p {shlex.quote(runtime_dir)}
if [ -f {shlex.quote(pid_file)} ]; then
  old_pid="$(cat {shlex.quote(pid_file)} 2>/dev/null || true)"
  if [ -n "$old_pid" ] && [ -d "/proc/$old_pid" ]; then
    echo "项目已在运行中（PID=$old_pid）"
    exit 11
  fi
  rm -f {shlex.quote(pid_file)}
fi
cd {shlex.quote(backend_path)}
{CONDA_INIT}
conda activate {shlex.quote(conda_name)}
nohup {command} >> {shlex.quote(log_file)} 2>&1 &
new_pid="$!"
if [ -z "$new_pid" ]; then
  echo "启动失败：无法获取PID"
  exit 12
fi
sleep 1
if ! kill -0 "$new_pid" 2>/dev/null; then
  echo "启动失败：进程未存活"
  exit 13
fi
echo "$new_pid" > {shlex.quote(pid_file)}
start_time="$(awk '{{print $22}}' /proc/$new_pid/stat)"
echo "$new_pid|$start_time|{mode}|{now}|$PORT_SELECTED" > {shlex.quote(meta_file)}
echo "已{launch_mode_text}启动：PID=$new_pid"
"""
  if configured_port:
    script = f'PORT_SELECTED={shlex.quote(configured_port)}\n' + script
  else:
    auto_port = _extract_port_from_command(command)
    script = f'PORT_SELECTED={shlex.quote(auto_port)}\n' + script

  code, out, err = await _run_shell(_safe_project_shell_script(script), timeout=30)
  if code == 11:
    raise HTTPException(status_code=400, detail=(out or err or '项目已在运行中').strip())
  if code != 0:
    raise HTTPException(status_code=500, detail=f'{launch_mode_text}启动失败：{(err or out or "unknown error").strip()}')
  actual_port = configured_port or _extract_port_from_command(command)
  if not actual_port:
    detect_cmd = (
      f'ss -lntpH 2>/dev/null | grep "pid=" | grep "pid=$new_pid," | '
      f"head -n 1 | sed -E 's#.*:([0-9]+)[[:space:]]+.*#\\1#'"
    )
    detect_script = f"""
set -euo pipefail
new_pid="$(cat {shlex.quote(pid_file)} 2>/dev/null || true)"
if [ -n "$new_pid" ]; then
  {detect_cmd}
fi
"""
    d_code, d_out, _d_err = await _run_shell(_safe_project_shell_script(detect_script), timeout=10)
    if d_code == 0:
      actual_port = (d_out or '').strip()

  if actual_port:
    return f'{(out or "").strip()} 端口={actual_port}'.strip()
  return (out or f'{launch_mode_text}启动成功').strip()


async def _stop_project_process(project) -> str:
  """停止项目进程。

  参数：
  - project：项目 ORM 对象。

  作用：
  - 只读取当前项目自己的 PID 文件和元数据文件。
  - 停止前校验 PID 与启动时间，避免 PID 复用导致误杀其他进程。

  返回：
  - 停止结果文案。
  """
  _, pid_file, meta_file = _build_project_runtime_paths(project.id)
  script = f"""
set -euo pipefail
if [ ! -f {shlex.quote(pid_file)} ]; then
  echo "未找到运行中的PID记录"
  exit 21
fi
pid="$(cat {shlex.quote(pid_file)} 2>/dev/null || true)"
meta="$(cat {shlex.quote(meta_file)} 2>/dev/null || true)"
if [ -z "$pid" ]; then
  rm -f {shlex.quote(pid_file)} {shlex.quote(meta_file)}
  echo "PID记录为空，已清理"
  exit 22
fi
if ! echo "$pid" | grep -Eq '^[0-9]+$'; then
  rm -f {shlex.quote(pid_file)} {shlex.quote(meta_file)}
  echo "PID记录非法，已清理"
  exit 23
fi
if [ ! -d "/proc/$pid" ]; then
  rm -f {shlex.quote(pid_file)} {shlex.quote(meta_file)}
  echo "进程不存在，已清理PID记录"
  exit 24
fi
meta_pid="$(echo "$meta" | awk -F'|' '{{print $1}}')"
meta_start="$(echo "$meta" | awk -F'|' '{{print $2}}')"
if [ "$meta_pid" != "$pid" ]; then
  echo "安全校验失败：PID与元数据不一致，拒绝停止"
  exit 25
fi
current_start="$(awk '{{print $22}}' /proc/$pid/stat)"
if [ -n "$meta_start" ] && [ "$current_start" != "$meta_start" ]; then
  echo "安全校验失败：进程启动时间不一致，拒绝停止"
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
  echo "停止失败：进程仍在运行（PID=$pid）"
  exit 27
fi
rm -f {shlex.quote(pid_file)} {shlex.quote(meta_file)}
echo "已停止：PID=$pid"
"""
  code, out, err = await _run_shell(_safe_project_shell_script(script), timeout=30)
  if code in {21, 22, 23, 24}:
    return (out or err or '未运行').strip()
  if code in {25, 26}:
    raise HTTPException(status_code=400, detail=(out or err or '安全校验失败，拒绝停止').strip())
  if code != 0:
    raise HTTPException(status_code=500, detail=f'停止失败：{(err or out or "unknown error").strip()}')
  return (out or '停止成功').strip()


async def _inspect_project_runtime(project) -> dict[str, str]:
  """检查项目运行时状态和实际监听端口。"""
  _, pid_file, meta_file = _build_project_runtime_paths(project.id)
  script = f"""
set -euo pipefail
pid_file={shlex.quote(pid_file)}
meta_file={shlex.quote(meta_file)}
if [ ! -f "$pid_file" ]; then
  echo "service_status=已停止"
  echo "running_port="
  exit 0
fi
pid="$(cat "$pid_file" 2>/dev/null || true)"
meta="$(cat "$meta_file" 2>/dev/null || true)"
if ! echo "$pid" | grep -Eq '^[0-9]+$'; then
  echo "service_status=已停止"
  echo "running_port="
  exit 0
fi
if [ ! -d "/proc/$pid" ]; then
  echo "service_status=已停止"
  echo "running_port="
  exit 0
fi
meta_pid="$(echo "$meta" | awk -F'|' '{{print $1}}')"
meta_start="$(echo "$meta" | awk -F'|' '{{print $2}}')"
meta_port="$(echo "$meta" | awk -F'|' '{{print $5}}')"
if [ -n "$meta_pid" ] && [ "$meta_pid" != "$pid" ]; then
  echo "service_status=已停止"
  echo "running_port="
  exit 0
fi
if [ -n "$meta_start" ]; then
  current_start="$(awk '{{print $22}}' /proc/$pid/stat 2>/dev/null || true)"
  if [ -n "$current_start" ] && [ "$current_start" != "$meta_start" ]; then
    echo "service_status=已停止"
    echo "running_port="
    exit 0
  fi
fi
port="$meta_port"
if [ -z "$port" ]; then
  port="$(ss -lntpH 2>/dev/null | grep "pid=$pid," | head -n 1 | sed -E 's#.*:([0-9]+)[[:space:]]+.*#\\1#' || true)"
fi
echo "service_status=运行中"
echo "running_port=$port"
"""
  code, out, _err = await _run_shell(_safe_project_shell_script(script), timeout=10)
  result = {'service_status': '已停止', 'running_port': ''}
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
