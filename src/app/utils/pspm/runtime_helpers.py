"""项目运行态路径、命令解析和终端展示工具。

本模块集中维护项目启动停止流程中的纯工具逻辑，包括 runtime 文件路径、启动命令端口合并、
入口文件路径解析、shell 标记解析，以及右侧终端区域展示步骤构造。
"""

from __future__ import annotations

import os
import re

from fastapi import HTTPException

from app.utils.pspm.path_utils import _normalize_path, _safe_command, _safe_optional_port_text
from app.utils.pspm.project_config import PORT_MAX, PORT_MIN, PROJECT_RUNTIME_BASE_DIR, SERVICE_BIND_HOST
from app.utils.pspm.shell_utils import _split_lines

MSG_NO_START_COMMAND = '暂无配置启动命令'
MSG_ENTRY_EMPTY = '项目入口文件位置不能为空'
MSG_ENTRY_INVALID = '项目入口文件位置不合法'
MSG_ENTRY_OUTSIDE = '项目入口文件位置超出项目目录'
MSG_ENTRY_MISSING = '项目入口文件不存在'
MSG_RUNNING = '运行中'
MSG_STOPPED = '已停止'
MSG_ALREADY_RUNNING = '项目已在运行中'
MSG_START_FAIL = '启动失败'
MSG_PID_EMPTY = '无法获取PID'
MSG_PROCESS_DEAD = '进程未存活'
MSG_START_TIME_FAIL = '无法读取进程启动时间'
MSG_FRONT = '前台'
MSG_BACK = '后台'
MSG_STARTED = '已{mode}启动：PID={pid}'
MSG_PORT = '端口'
MSG_UNKNOWN = '未知'
MSG_STOP_SUCCESS = '停止成功'
MSG_NOT_RUNNING = '未运行'
MSG_STOP_FAIL = '停止失败'
MSG_SECURITY_FAIL = '安全校验失败，拒绝停止'
MSG_PID_RECORD_MISSING = '未找到运行中的PID记录'
MSG_PID_RECORD_EMPTY = 'PID记录为空，已清理'
MSG_PID_RECORD_INVALID = 'PID记录非法，已清理'
MSG_PROCESS_MISSING_CLEANED = '进程不存在，已清理PID记录'
MSG_PID_META_MISMATCH = '安全校验失败：PID与元数据不一致，拒绝停止'
MSG_START_TIME_MISMATCH = '安全校验失败：进程启动时间不一致，拒绝停止'
MSG_STILL_RUNNING = '停止失败：进程仍在运行'
MSG_NO_PROCESS_INFO = '未获取到进程信息'
MSG_NO_LISTEN_PORT = '未检测到监听端口'
MSG_CURRENT_RECORD_PORT = '当前记录端口'
MSG_NO_LOG = '暂无运行日志输出'
MSG_NO_PID = '未找到 PID'
MSG_ENTER_DIR_OK = '已进入目录'
MSG_CONDA_OK = 'Conda环境已激活'
MSG_SERVICE_STARTING = '服务启动中'
MSG_SERVICE_CHECK_OK = '后端已检测进程和端口'
MSG_FRONT_HINT = '前台启动由系统保持运行，可在终端使用 Ctrl+C 停止'


def _project_runtime_dir(project_id: int) -> str:
  """返回项目运行态目录。

  参数：
  - project_id：项目表主键 ID。

  作用：
  - 统一保存该项目的 PID、meta、运行日志等临时运行数据。

  返回：
  - 项目运行态目录路径。
  """
  return f'{PROJECT_RUNTIME_BASE_DIR}/project_{project_id}'

def _project_pid_file(project_id: int) -> str:
  """返回项目 PID 记录文件路径。"""
  return f'{_project_runtime_dir(project_id)}/service.pid'

def _project_meta_file(project_id: int) -> str:
  """返回项目运行元数据文件路径。"""
  return f'{_project_runtime_dir(project_id)}/service.meta'

def _project_log_file(project_id: int) -> str:
  """返回项目运行日志文件路径。"""
  return f'{_project_runtime_dir(project_id)}/service.log'

def _build_project_runtime_paths(project_id: int) -> tuple[str, str, str]:
  """一次性返回运行态目录、PID 文件、meta 文件路径。"""
  return _project_runtime_dir(project_id), _project_pid_file(project_id), _project_meta_file(project_id)

def _extract_port_from_command(command: str) -> str:
  """从启动命令中提取端口号。

  参数：
  - command：开发启动命令或部署启动命令。

  作用：
  - 支持识别 `--port`、`-p`、`host:port` 等常见写法。
  - 启动后用于回填正在运行端口。

  返回：
  - 命中合法端口时返回端口字符串，否则返回空字符串。
  """
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
  """判断命令去掉左侧空白后是否以指定命令词开头。"""
  cmd = str(command or '').lstrip()
  return cmd.startswith(token)

def _apply_configured_port(command: str, configured_port: str) -> str:
  """把项目配置中的端口应用到启动命令。

  参数：
  - command：原始启动命令。
  - configured_port：项目配置中的后端部署端口。

  作用：
  - 当命令本身已有端口参数时替换端口。
  - 对 gunicorn、uvicorn、Django runserver 等常见命令做兼容。

  返回：
  - 替换端口后的启动命令。
  """
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
      return re.sub(r'(--bind(?:=|\s+))([^ \t]+)', rf'\g<1>{SERVICE_BIND_HOST}:{port}', cmd, count=1)
    if re.search(r'(?<!\w)-b(?:=|\s+)[^ \t]+', cmd):
      return re.sub(r'((?<!\w)-b(?:=|\s+))([^ \t]+)', rf'\g<1>{SERVICE_BIND_HOST}:{port}', cmd, count=1)
    return f'{cmd} --bind {SERVICE_BIND_HOST}:{port}'

  if _starts_with_token(cmd, 'uvicorn'):
    return f'{cmd} --port {port}'

  if _starts_with_token(cmd, 'python '):
    return f'{cmd} --port {port}'

  raise HTTPException(
    status_code=400,
    detail='已设置启动端口，请在启动命令中使用 {port}、-port/-p 或 --bind 参数',
  )

def _get_raw_start_command(project, mode: str) -> str:
  """读取指定启动模式的原始启动命令。

  参数：
  - project：项目 ORM 对象。
  - mode：启动模式，`deploy` 表示部署启动，其他值表示开发启动。

  返回：
  - 对应的启动命令字符串。

  异常：
  - 未配置命令时抛出 HTTP 400。
  """
  if mode == 'deploy':
    raw_cmd = str(project.deploy_start_command or '').strip()
  else:
    raw_cmd = str(project.dev_start_command or '').strip()
  if not raw_cmd:
    raise HTTPException(status_code=400, detail=MSG_NO_START_COMMAND)
  return raw_cmd

def _resolve_start_command(project, mode: str) -> tuple[str, str]:
  """解析最终执行的启动命令和端口。

  参数：
  - project：项目 ORM 对象。
  - mode：启动模式。

  作用：
  - 根据开发/部署模式读取不同命令。
  - 将 `{port}` 或配置端口合并到命令中。

  返回：
  - `(command, selected_port)`，分别是最终命令和选中的端口。
  """
  selected_port = ''
  if mode == 'deploy':
    raw_cmd = _safe_command(_get_raw_start_command(project, mode), '部署启动命令')
    selected_port = _safe_optional_port_text(project.backend_deploy_port or '')
    cmd = raw_cmd
    if '{port}' in raw_cmd:
      if not selected_port:
        raise HTTPException(status_code=400, detail='部署启动命令包含 {port}，请先配置后端部署端口')
      cmd = raw_cmd.replace('{port}', selected_port)
    elif selected_port:
      cmd = _apply_configured_port(raw_cmd, selected_port)
    return cmd, selected_port

  raw_cmd = _safe_command(_get_raw_start_command(project, mode), '开发启动命令')
  selected_port = _safe_optional_port_text(project.backend_dev_port or '')
  cmd = raw_cmd
  if '{port}' in raw_cmd:
    if not selected_port:
      raise HTTPException(status_code=400, detail='开发启动命令包含 {port}，请先配置后端开发端口')
    cmd = raw_cmd.replace('{port}', selected_port)
  elif selected_port:
    cmd = _apply_configured_port(raw_cmd, selected_port)
  return cmd, selected_port

def _resolve_entry_file_abs_path(project) -> tuple[str, str]:
  """解析项目入口文件绝对路径。

  参数：
  - project：项目 ORM 对象，读取 backend_path 和 entry_file_path。

  返回：
  - `(abs_path, display_path)`，分别用于远端检测和错误提示。

  异常：
  - 入口为空、不合法、越出项目目录时抛出 HTTP 400。
  """
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

def _extract_marked_value(output: str, key: str) -> str:
  """从 shell 输出中提取 `KEY=value` 标记值。"""
  prefix = f'{key}='
  for line in _split_lines(output):
    if line.startswith(prefix):
      return line[len(prefix):].strip()
  return ''

def _extract_marked_block(output: str, begin_key: str, end_key: str) -> str:
  """从 shell 输出中提取 begin/end 标记之间的多行文本。"""
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
  """生成启动服务时展示到右侧终端区域的步骤列表。

  参数来自启动流程解析结果，包括工作目录、Conda 环境、启动命令、PID 和端口。

  返回：
  - 前端终端区域可逐条渲染的步骤字典列表。
  """
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

def _build_stop_terminal_steps(pid: str, meta_file: str, pid_file: str, output: str) -> list[dict[str, str]]:
  """生成停止服务时展示到右侧终端区域的步骤列表。"""
  return [
    {'type': 'command', 'text': f'cat {pid_file}'},
    {'type': 'output', 'text': pid or MSG_NO_PID},
    {'type': 'command', 'text': f'cat {meta_file}'},
    {'type': 'command', 'text': f'kill {pid}' if pid else 'kill <PID>'},
    {'type': 'output', 'text': output or MSG_STOP_SUCCESS},
  ]

def _strip_internal_runtime_markers(text: str) -> str:
  """移除 shell 内部 PSPM_* 标记，只保留用户可读输出。"""
  visible_lines = []
  for line in str(text or '').splitlines():
    value = line.strip()
    if not value or value.startswith('PSPM_'):
      continue
    if visible_lines and visible_lines[-1] == value:
      continue
    visible_lines.append(value)
  return '\n'.join(visible_lines).strip()
