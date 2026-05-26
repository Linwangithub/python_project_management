"""项目运行服务模块，负责启动、停止和检测项目服务运行状态。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from fastapi import HTTPException

from app import crud
from app.services.pspm.project_detail import record_project_operation
from app.services.pspm.project_helpers import get_project_for_user
from app.utils.pspm.runtime_utils import (
  _finalize_project_foreground_start,
  _prepare_project_foreground_start,
  _start_project_process,
  _stop_project_process,
)
from app.utils.pspm.shell_utils import _find_server_row_by_id, _list_allowed_server_rows

MSG_NO_SERVER_PERMISSION = '当前用户无该项目服务器使用权限'
MSG_DEPLOY_START = '部署启动'
MSG_START_BACKGROUND = '后台启动'
MSG_START_FOREGROUND = '前台启动'
MSG_STOP_SERVICE = '停止服务'
MSG_STOPPED = '已停止'
MSG_RUNNING = '运行中'
MSG_COPY = '复制项目'
MSG_EXPORT = '导出项目'


def _runtime_message(result) -> str:
  """格式化项目运行工具层返回的提示信息。

  参数：
  - result：运行工具层返回的 dict 或字符串。

  返回：
  - 用户可读的 message 文本；没有 message 时返回空字符串。
  """
  if isinstance(result, dict):
    return str(result.get('message') or '').strip()
  return str(result or '').strip()


async def _get_project_server_row(session, current_user, project):
  """查询当前用户可使用的项目服务器记录。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。
  - project：项目 ORM 对象。

  返回：
  - 项目绑定且当前用户有权限使用的服务器对象。

  异常：
  - 当前用户无该服务器权限时抛出 HTTP 403。
  """
  servers = await _list_allowed_server_rows(session, current_user)
  server_row = _find_server_row_by_id(servers, getattr(project, 'server_id', None))
  if not server_row:
    raise HTTPException(status_code=403, detail=MSG_NO_SERVER_PERMISSION)
  return server_row


async def prepare_project_foreground_service(session, current_user, project_id: int) -> dict:
  """准备 WebSocket 前台启动需要的上下文。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。
  - project_id：项目 ID。

  返回：
  - 目标服务器 IP、工作目录、Conda 环境、启动命令、端口等信息。
  """
  project, _is_root = await get_project_for_user(session, project_id, current_user)
  server_row = await _get_project_server_row(session, current_user, project)
  data = await _prepare_project_foreground_start(server_row=server_row, project=project)
  data['server_ip'] = getattr(server_row, 'ip', '')
  return data


async def finalize_project_foreground_service(
  session,
  current_user,
  project_id: int,
  pid: str,
  port: str,
  log_file: str = '',
) -> dict:
  """完成 WebSocket 前台启动后的状态落库和操作日志记录。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。
  - project_id：项目 ID。
  - pid：前台启动进程 ID。
  - port：运行端口。
  - log_file：运行日志文件路径。

  返回：
  - 前台启动最终结果。
  """
  project, _is_root = await get_project_for_user(session, project_id, current_user)
  server_row = await _get_project_server_row(session, current_user, project)
  result = await _finalize_project_foreground_start(
    server_row=server_row,
    project=project,
    pid=pid,
    port=port,
    log_file=log_file,
  )
  message = _runtime_message(result)
  await crud.projects.update_status(session, project_id=project_id, running=True)
  await record_project_operation(
    session,
    project,
    current_user,
    action='start_foreground',
    action_label=MSG_START_FOREGROUND,
    summary=f'{MSG_START_FOREGROUND}：{project.name}',
    before_data={'status': MSG_STOPPED},
    after_data={'status': MSG_RUNNING},
    detail={**result, 'server_ip': getattr(server_row, 'ip', '')},
  )
  return result if isinstance(result, dict) else {'message': message}


async def start_project_service(session, current_user, project_id: int, mode: str, run_in_background: bool) -> dict:
  """执行后台启动或部署启动并记录操作日志。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。
  - project_id：项目 ID。
  - mode：启动模式，dev/deploy。
  - run_in_background：是否后台运行。

  返回：
  - 启动结果、运行端口、终端展示步骤等信息。
  """
  project, _is_root = await get_project_for_user(session, project_id, current_user)
  server_row = await _get_project_server_row(session, current_user, project)
  result = await _start_project_process(
    server_row=server_row,
    project=project,
    mode=mode,
    run_in_background=run_in_background,
  )
  message = _runtime_message(result)
  await crud.projects.update_status(session, project_id=project_id, running=True)
  if mode == 'deploy':
    action, label = 'deploy_start', MSG_DEPLOY_START
  elif run_in_background:
    action, label = 'start_background', MSG_START_BACKGROUND
  else:
    action, label = 'start_foreground', MSG_START_FOREGROUND
  await record_project_operation(
    session,
    project,
    current_user,
    action=action,
    action_label=label,
    summary=f'{label}：{project.name}',
    before_data={'status': MSG_STOPPED},
    after_data={'status': MSG_RUNNING},
    detail={**(result if isinstance(result, dict) else {'message': message}), 'server_ip': getattr(server_row, 'ip', '')},
  )
  return result if isinstance(result, dict) else {'message': message}


async def stop_project_service(session, current_user, project_id: int) -> dict:
  """停止项目服务并记录停止日志。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。
  - project_id：项目 ID。

  返回：
  - 停止结果、PID 和终端展示步骤。
  """
  project, _is_root = await get_project_for_user(session, project_id, current_user)
  server_row = await _get_project_server_row(session, current_user, project)
  result = await _stop_project_process(server_row, project)
  message = _runtime_message(result)
  await crud.projects.update_status(session, project_id=project_id, running=False)
  await record_project_operation(
    session,
    project,
    current_user,
    action='stop',
    action_label=MSG_STOP_SERVICE,
    summary=f'{MSG_STOP_SERVICE}：{project.name}',
    before_data={'status': MSG_RUNNING},
    after_data={'status': MSG_STOPPED},
    detail={**(result if isinstance(result, dict) else {'message': message}), 'server_ip': getattr(server_row, 'ip', '')},
  )
  return result if isinstance(result, dict) else {'message': message}


async def copy_project_service(session, current_user, project_id: int, target_server_ip: str, target_dir: str) -> str:
  """记录复制项目操作。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。
  - project_id：项目 ID。
  - target_server_ip：目标服务器 IP。
  - target_dir：目标目录。

  返回：
  - 复制任务提示文本。
  """
  project, _is_root = await get_project_for_user(session, project_id, current_user)
  message = f'复制任务已下发到 {target_server_ip}:{target_dir}'
  await record_project_operation(
    session,
    project,
    current_user,
    action='copy',
    action_label=MSG_COPY,
    summary=f'{MSG_COPY}：{project.name}',
    before_data=None,
    after_data={'target_server_ip': target_server_ip, 'target_dir': target_dir},
    detail={'message': message},
  )
  return message


async def export_project_service(session, current_user, project_id: int, target_dir: str) -> str:
  """记录导出项目操作。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。
  - project_id：项目 ID。
  - target_dir：导出目标目录。

  返回：
  - 导出任务提示文本。
  """
  project, _is_root = await get_project_for_user(session, project_id, current_user)
  message = f'导出任务已下发到本机目录 {target_dir}'
  await record_project_operation(
    session,
    project,
    current_user,
    action='export',
    action_label=MSG_EXPORT,
    summary=f'{MSG_EXPORT}：{project.name}',
    before_data=None,
    after_data={'target_dir': target_dir},
    detail={'message': message},
  )
  return message
