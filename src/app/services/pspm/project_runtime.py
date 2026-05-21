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

MSG_NO_SERVER_PERMISSION = '\u5f53\u524d\u7528\u6237\u65e0\u8be5\u9879\u76ee\u670d\u52a1\u5668\u4f7f\u7528\u6743\u9650'
MSG_DEPLOY_START = '\u90e8\u7f72\u542f\u52a8'
MSG_START_BACKGROUND = '\u540e\u53f0\u542f\u52a8'
MSG_START_FOREGROUND = '\u524d\u53f0\u542f\u52a8'
MSG_STOP_SERVICE = '\u505c\u6b62\u670d\u52a1'
MSG_STOPPED = '\u5df2\u505c\u6b62'
MSG_RUNNING = '\u8fd0\u884c\u4e2d'
MSG_COPY = '\u590d\u5236\u9879\u76ee'
MSG_EXPORT = '\u5bfc\u51fa\u9879\u76ee'


def _runtime_message(result) -> str:
  if isinstance(result, dict):
    return str(result.get('message') or '').strip()
  return str(result or '').strip()


async def _get_project_server_row(session, current_user, project):
  servers = await _list_allowed_server_rows(session, current_user)
  server_row = _find_server_row_by_id(servers, getattr(project, 'server_id', None))
  if not server_row:
    raise HTTPException(status_code=403, detail=MSG_NO_SERVER_PERMISSION)
  return server_row


async def prepare_project_foreground_service(session, current_user, project_id: int) -> dict:
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
    summary=f'{label}\uff1a{project.name}',
    before_data={'status': MSG_STOPPED},
    after_data={'status': MSG_RUNNING},
    detail={**(result if isinstance(result, dict) else {'message': message}), 'server_ip': getattr(server_row, 'ip', '')},
  )
  return result if isinstance(result, dict) else {'message': message}


async def stop_project_service(session, current_user, project_id: int) -> dict:
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
    summary=f'{MSG_STOP_SERVICE}\uff1a{project.name}',
    before_data={'status': MSG_RUNNING},
    after_data={'status': MSG_STOPPED},
    detail={**(result if isinstance(result, dict) else {'message': message}), 'server_ip': getattr(server_row, 'ip', '')},
  )
  return result if isinstance(result, dict) else {'message': message}


async def copy_project_service(session, current_user, project_id: int, target_server_ip: str, target_dir: str) -> str:
  project, _is_root = await get_project_for_user(session, project_id, current_user)
  message = f'\u590d\u5236\u4efb\u52a1\u5df2\u4e0b\u53d1\u5230 {target_server_ip}:{target_dir}'
  await record_project_operation(
    session,
    project,
    current_user,
    action='copy',
    action_label=MSG_COPY,
    summary=f'{MSG_COPY}\uff1a{project.name}',
    before_data=None,
    after_data={'target_server_ip': target_server_ip, 'target_dir': target_dir},
    detail={'message': message},
  )
  return message


async def export_project_service(session, current_user, project_id: int, target_dir: str) -> str:
  project, _is_root = await get_project_for_user(session, project_id, current_user)
  message = f'\u5bfc\u51fa\u4efb\u52a1\u5df2\u4e0b\u53d1\u5230\u672c\u673a\u76ee\u5f55 {target_dir}'
  await record_project_operation(
    session,
    project,
    current_user,
    action='export',
    action_label=MSG_EXPORT,
    summary=f'{MSG_EXPORT}\uff1a{project.name}',
    before_data=None,
    after_data={'target_dir': target_dir},
    detail={'message': message},
  )
  return message
