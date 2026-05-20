from app import crud
from app.services.pspm.project_detail import record_project_operation
from app.services.pspm.project_helpers import get_project_for_user
from app.utils.pspm.runtime_utils import _start_project_process, _stop_project_process


async def start_project_service(session, current_user, project_id: int, mode: str, run_in_background: bool) -> str:
  """启动项目服务。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。
  - project_id：项目 ID，来自 Query 参数。
  - mode：启动模式，`dev` 表示开发/前台或后台启动，`deploy` 表示部署启动。
  - run_in_background：是否后台运行。

  作用：
  - 前台启动、后台启动、部署启动三个接口共用该函数。
  - 内部会校验项目权限，调用 runtime 工具生成并执行启动命令。
  - 启动成功后把项目状态更新为运行中。

  返回：
  - 启动结果文案，用于接口层返回给前端。
  """
  project, _is_root = await get_project_for_user(session, project_id, current_user)
  message = await _start_project_process(project=project, mode=mode, run_in_background=run_in_background)
  await crud.projects.update_status(session, project_id=project_id, running=True)
  if mode == 'deploy':
    action, label = 'deploy_start', '部署启动'
  elif run_in_background:
    action, label = 'start_background', '后台启动'
  else:
    action, label = 'start_foreground', '前台启动'
  await record_project_operation(
    session,
    project,
    current_user,
    action=action,
    action_label=label,
    summary=f'{label}项目：{project.name}',
    before_data={'status': '已停止'},
    after_data={'status': '运行中'},
    detail={'message': message, 'mode': mode, 'run_in_background': run_in_background},
  )
  return message


async def stop_project_service(session, current_user, project_id: int) -> str:
  """停止项目服务。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。
  - project_id：项目 ID，来自 Query 参数。

  作用：
  - 只停止当前项目 runtime 元数据记录的 PID，避免误杀其他 Python 或系统进程。
  - 停止成功后把项目状态更新为已停止。

  返回：
  - 停止结果文案，用于接口层返回给前端。
  """
  project, _is_root = await get_project_for_user(session, project_id, current_user)
  message = await _stop_project_process(project)
  await crud.projects.update_status(session, project_id=project_id, running=False)
  await record_project_operation(
    session,
    project,
    current_user,
    action='stop',
    action_label='停止服务',
    summary=f'停止项目服务：{project.name}',
    before_data={'status': '运行中'},
    after_data={'status': '已停止'},
    detail={'message': message},
  )
  return message


async def copy_project_service(session, current_user, project_id: int, target_server_ip: str, target_dir: str) -> str:
  """下发项目复制任务。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。
  - project_id：项目 ID。
  - target_server_ip：目标服务器 IP，来自复制弹框。
  - target_dir：目标目录，来自复制弹框。

  作用：
  - 当前版本只做权限校验并返回任务文案。
  - 后续可以在这里接入真实复制逻辑，不影响接口层。

  返回：
  - 复制任务提示文案。
  """
  project, _is_root = await get_project_for_user(session, project_id, current_user)
  message = f'复制任务已下发到 {target_server_ip}:{target_dir}'
  await record_project_operation(
    session,
    project,
    current_user,
    action='copy',
    action_label='复制项目',
    summary=f'复制项目：{project.name}',
    before_data=None,
    after_data={'target_server_ip': target_server_ip, 'target_dir': target_dir},
    detail={'message': message},
  )
  return message


async def export_project_service(session, current_user, project_id: int, target_dir: str) -> str:
  """下发项目导出任务。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户。
  - project_id：项目 ID。
  - target_dir：导出目录，来自导出弹框。

  作用：
  - 当前版本只做权限校验并返回任务文案。
  - 后续可以在这里接入真实打包导出逻辑。

  返回：
  - 导出任务提示文案。
  """
  project, _is_root = await get_project_for_user(session, project_id, current_user)
  message = f'导出任务已下发到本机目录 {target_dir}'
  await record_project_operation(
    session,
    project,
    current_user,
    action='export',
    action_label='导出项目',
    summary=f'导出项目：{project.name}',
    before_data=None,
    after_data={'target_dir': target_dir},
    detail={'message': message},
  )
  return message
