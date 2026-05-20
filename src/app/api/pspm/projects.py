from typing import List

from fastapi import APIRouter, Depends, Query

from app import crud, schemas
from app.api.deps import require_permission
from app.core.deps import SessionDep
from app.services.pspm.project_checks import (
  check_project_database_service,
  check_project_name_service,
  check_project_nginx_service,
  check_project_port_service,
  list_project_conda_envs_service,
  list_project_entry_path_children_service,
  inspect_projects_health_service,
  inspect_project_health_service,
)
from app.services.pspm.project_create import create_project_real_service, create_project_record_service
from app.services.pspm.project_detail import get_project_detail_service, list_project_logs_service
from app.services.pspm.project_delete import delete_project_service
from app.services.pspm.project_runtime import (
  copy_project_service,
  export_project_service,
  start_project_service,
  stop_project_service,
)
from app.services.pspm.project_setting import (
  delete_original_project_database_service,
  update_project_setting_service,
)
from app.services.pspm.project_sync import (
  check_sync_conda_service,
  check_sync_database_service,
  check_sync_nginx_server_block_service,
  list_sync_nginx_server_port_options_service,
  list_sync_conda_envs_service,
  list_sync_entry_path_children_service,
  list_sync_project_path_children_service,
  sync_existing_project_service,
)
from app.utils.pspm.project_config import DELETE_SCOPE_PROJECT_ONLY

# 项目管理路由对象：被 app/api/api.py 通过 /pspm/projects 前缀挂载。
router = APIRouter()


@router.get('/entry-path-children', name='入口文件路径子项', response_model=schemas.pspm.ProjectEntryPathChildrenResponse)
async def list_project_entry_path_children(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'setting')),
  project_id: int = Query(..., description='项目ID'),
  rel_path: str = Query('', description='相对项目根路径'),
):
  """获取入口文件选择器的子目录/文件。

  参数：
  - session：数据库会话，由 FastAPI 依赖 `SessionDep` 注入。
  - current_user：当前登录用户，由 `require_permission('project_management', 'setting')` 校验权限后注入。
  - project_id：项目 ID，来自 Query 参数，用于定位项目根目录。
  - rel_path：相对项目根目录路径，来自前端级联选择器当前节点。

  作用：
  - 设置弹框中选择项目入口文件时，前端逐层请求该接口展示下一层目录和文件。

  返回：
  - `ProjectEntryPathChildrenResponse`，data 为可选节点列表。
  """
  nodes = await list_project_entry_path_children_service(session, current_user, project_id, rel_path)
  return schemas.pspm.ProjectEntryPathChildrenResponse(data=nodes)


@router.get('', name='列表', response_model=schemas.pspm.ProjectItemsResponse)
async def list_projects(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', None)),
  page: int = Query(1, description='页码'),
  page_size: int = Query(20, description='每页数量'),
  owner_id: int | None = Query(None, description='按用户筛选'),
):
  """查询项目列表。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，只要求项目管理菜单可见。
  - page/page_size：分页参数，来自 Query。
  - owner_id：root 用户可按成员筛选；普通用户会被强制改为自己。

  作用：
  - 项目管理主列表使用该接口加载数据。
  - root 看到所有项目，普通用户只能看到自己的项目。

  返回：
  - `ProjectItemsResponse`，包含分页总数和项目行数据。
  """
  is_root = await crud.rbac.is_root_user(session, user_id=current_user.id)
  if not is_root:
    owner_id = current_user.id

  result = await crud.projects.get_items(
    session,
    current_user_id=current_user.id,
    is_root=is_root,
    owner_id=owner_id,
    page=page,
    page_size=page_size,
  )
  # 列表接口只返回基础字段，项目健康状态改为点击按钮后按需检测，避免刷新页面时批量检测拖慢或报错。
  return schemas.pspm.ProjectItemsResponse(data=result)



@router.get('/health', name='项目健康检测', response_model=schemas.pspm.ProjectHealthCheckResponse)
async def check_project_health(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', None)),
  project_id: int = Query(..., description='项目ID'),
):
  """按需检测单个项目健康状态。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，只要求项目管理菜单可见。
  - project_id：项目 ID，来自项目状态列按钮。

  作用：
  - 避免项目列表刷新时批量检测所有项目导致页面慢或某个项目异常拖垮整个列表。
  - 点击某一行“检测状态”按钮时，只检测当前项目。

  返回：
  - ProjectHealthCheckResponse，data 为带检测结果的项目行结构。
  """
  data = await inspect_project_health_service(session, current_user, project_id)
  return schemas.pspm.ProjectHealthCheckResponse(data=data)


@router.get('/detail', name='项目详情', response_model=schemas.pspm.ProjectDetailResponse)
async def get_project_detail(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', None)),
  project_id: int = Query(..., description='项目ID'),
):
  """查询项目完整详情。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，只要求项目管理菜单可见。
  - project_id：项目 ID，来自详情按钮所在行。

  作用：
  - 详情侧边栏调用该接口展示项目所有已配置的信息。
  - 包含 Conda 环境位置、Conda 中 Python 版本、数据库、Nginx、启动命令等信息。

  返回：
  - `ProjectDetailResponse`，data.sections 为前端分组展示数据。
  """
  data = await get_project_detail_service(session, current_user, project_id)
  return schemas.pspm.ProjectDetailResponse(data=data)


@router.get('/logs', name='项目操作日志', response_model=schemas.pspm.ProjectOperationLogsResponse)
async def list_project_logs(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', None)),
  project_id: int = Query(..., description='项目ID'),
):
  """查询项目操作日志。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，只要求项目管理菜单可见。
  - project_id：项目 ID，来自日志按钮所在行。

  作用：
  - 日志弹框调用该接口展示项目创建、配置修改、启动停止等操作记录。
  - 修改配置日志会包含修改前、修改后和字段变化明细。

  返回：
  - `ProjectOperationLogsResponse`，包含操作日志列表。
  """
  data = await list_project_logs_service(session, current_user, project_id)
  return schemas.pspm.ProjectOperationLogsResponse(data=data)


@router.get('/conda-envs', name='查询Conda环境列表', response_model=schemas.pspm.ProjectCondaEnvListResponse)
async def list_project_conda_envs(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'setting')),
  project_id: int = Query(..., description='项目ID'),
):
  """查询项目所在服务器的 Conda 环境列表。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要项目设置权限。
  - project_id：项目 ID，来自 Query。

  作用：
  - 设置弹框 Conda 步骤用于展示已有环境。
  - 前端也根据该列表判断用户输入的新环境名是否冲突。

  返回：
  - `ProjectCondaEnvListResponse`，包含 Conda 环境目录和环境名列表。
  """
  data = await list_project_conda_envs_service(session, current_user, project_id)
  return schemas.pspm.ProjectCondaEnvListResponse(data=data)


@router.get('/check-name', name='检查项目名', response_model=schemas.pspm.ProjectNameCheckResponse)
async def check_project_name(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'create')),
  name: str = Query(..., description='项目名称'),
  base_path: str = Query(..., description='项目基础路径'),
  server_ip: str = Query(..., description='服务器IP'),
):
  """检查项目名称对应目录是否已存在。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要创建项目权限。
  - name：项目名称，来自新建项目弹框。
  - base_path：项目基础路径，例如 `/root/project`。
  - server_ip：业务服务器 IP，用于校验当前用户是否可使用该服务器。

  作用：
  - 前端项目名称输入框失去焦点时调用。
  - 避免真实创建时才发现项目目录重复。

  返回：
  - `ProjectNameCheckResponse`，包含是否存在和最终项目目录。
  """
  data = await check_project_name_service(session, current_user, name, base_path, server_ip)
  return schemas.pspm.ProjectNameCheckResponse(data=data)


@router.post('/check-database', name='检查数据库连接', response_model=schemas.pspm.ProjectDatabaseCheckResponse)
async def check_project_database(
  *,
  current_user=Depends(require_permission('project_management', 'create')),
  payload: schemas.pspm.ProjectDatabaseCheckRequest,
):
  """检查数据库连接和目标库是否可创建。

  参数：
  - current_user：当前登录用户，需要创建项目权限；该变量只用于权限依赖。
  - payload：数据库连接信息，请求体来自创建项目或设置项目弹框。

  作用：
  - 只在连接成功且目标数据库不存在时允许前端继续创建/修改。
  - 失败原因对前端统一暴露为连接失败，避免泄露底层错误。

  返回：
  - `ProjectDatabaseCheckResponse`，包含连接结果、数据库是否存在、是否可创建。
  """
  _ = current_user
  data = await check_project_database_service(payload)
  return schemas.pspm.ProjectDatabaseCheckResponse(data=data)


@router.post('/check-nginx', name='检查Nginx', response_model=schemas.pspm.ProjectNginxCheckResponse)
async def check_project_nginx(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'create')),
  payload: schemas.pspm.ProjectNginxCheckRequest,
):
  """检查 Nginx 服务是否可用并返回配置文件清单。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要创建项目权限。
  - payload：项目服务器 IP 和 Nginx 服务器 IP。

  作用：
  - 新建项目启用 Nginx 时先检测服务器连通性和 Nginx 运行状态。
  - 同时返回正在运行主配置及 include 展开的可选配置文件。

  返回：
  - `ProjectNginxCheckResponse`，包含配置文件路径、可新建目录和提示信息。
  """
  data = await check_project_nginx_service(session, current_user, payload)
  return schemas.pspm.ProjectNginxCheckResponse(data=data)


@router.post('/check-port', name='检查端口', response_model=schemas.pspm.ProjectPortCheckResponse)
async def check_project_port(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'setting')),
  payload: schemas.pspm.ProjectPortCheckRequest,
):
  """检查端口是否可用。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要项目设置权限。
  - payload：端口、项目 ID、Nginx 服务器 IP、是否检查 Nginx 配置。

  作用：
  - 创建项目和设置项目中的 Nginx 前端端口、后端部署端口都调用该接口。
  - 同时判断系统监听端口、Nginx listen 和 proxy_pass 中是否已有冲突。

  返回：
  - `ProjectPortCheckResponse`，成功时表示端口可用。
  """
  data = await check_project_port_service(session, current_user, payload)
  return schemas.pspm.ProjectPortCheckResponse(data=data)


@router.post('/sync/path-children', name='同步项目目录子项', response_model=schemas.pspm.ProjectSyncPathChildrenResponse)
async def list_sync_project_path_children(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'create')),
  payload: schemas.pspm.ProjectSyncPathChildrenRequest,
):
  """查询同步已有项目时可选择的项目目录子项。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要项目创建权限。
  - payload：服务器 IP 和相对项目根目录路径。

  作用：
  - 同步已有项目弹框使用该接口逐层选择已经存在的项目目录。
  - 后端强制目录位于 `/root/project` 或 `/home/{username}/project` 配置前缀下。

  返回：
  - ProjectSyncPathChildrenResponse，data 为目录节点列表。
  """
  data = await list_sync_project_path_children_service(session, current_user, payload)
  return schemas.pspm.ProjectSyncPathChildrenResponse(data=data)


@router.post('/sync/entry-path-children', name='同步项目入口文件子项', response_model=schemas.pspm.ProjectSyncEntryPathChildrenResponse)
async def list_sync_entry_path_children(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'create')),
  payload: schemas.pspm.ProjectSyncEntryPathChildrenRequest,
):
  """查询同步已有项目时入口文件选择器的子项。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要项目创建权限。
  - payload：服务器 IP、已选择项目目录和当前相对路径。

  作用：
  - 同步已有项目弹框在选择项目目录后，逐层选择具体入口文件。
  - 后端只允许读取已选择项目目录内部的文件和文件夹。

  返回：
  - ProjectSyncEntryPathChildrenResponse，data 为目录/文件节点列表。
  """
  data = await list_sync_entry_path_children_service(session, current_user, payload)
  return schemas.pspm.ProjectSyncEntryPathChildrenResponse(data=data)


@router.post('/sync/conda-envs', name='同步项目Conda环境列表', response_model=schemas.pspm.ProjectSyncCondaEnvListResponse)
async def list_sync_conda_envs(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'create')),
  payload: schemas.pspm.ProjectSyncCondaEnvListRequest,
):
  """查询同步已有项目时某台服务器上的 Conda 环境列表。"""
  data = await list_sync_conda_envs_service(session, current_user, payload)
  return schemas.pspm.ProjectSyncCondaEnvListResponse(data=data)


@router.post('/sync/check-conda', name='同步项目检查Conda环境', response_model=schemas.pspm.ProjectSyncCondaCheckResponse)
async def check_sync_conda(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'create')),
  payload: schemas.pspm.ProjectSyncCondaCheckRequest,
):
  """检查同步已有项目选择的 Conda 环境是否存在，并返回实际 Python 版本。"""
  data = await check_sync_conda_service(session, current_user, payload)
  return schemas.pspm.ProjectSyncCondaCheckResponse(data=data)


@router.post('/sync/check-database', name='同步项目检查数据库', response_model=schemas.pspm.ProjectSyncDatabaseCheckResponse)
async def check_sync_database(
  *,
  current_user=Depends(require_permission('project_management', 'create')),
  payload: schemas.pspm.ProjectSyncDatabaseCheckRequest,
):
  """检查同步已有项目绑定的数据库是否已经存在且可连接。"""
  _ = current_user
  data = await check_sync_database_service(payload)
  return schemas.pspm.ProjectDatabaseCheckResponse(data=data)


@router.post('/sync/nginx-server-port-options', name='同步项目Nginx端口选项', response_model=schemas.pspm.ProjectSyncNginxServerPortOptionsResponse)
async def list_sync_nginx_server_port_options(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'create')),
  payload: schemas.pspm.ProjectSyncNginxServerPortOptionsRequest,
):
  """查询同步已有项目时某个 Nginx 配置文件内已有的端口组合。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要项目创建权限。
  - payload：项目服务器 IP、Nginx 服务器 IP、已选择的配置文件路径。

  作用：
  - 同步已有项目不是创建新端口，而是选择已有 Nginx server 块。
  - 返回 listen 前端端口下拉选项，并携带同块 proxy_pass 后端端口。

  返回：
  - ProjectSyncNginxServerPortOptionsResponse，data.options 为端口组合列表。
  """
  data = await list_sync_nginx_server_port_options_service(session, current_user, payload)
  return schemas.pspm.ProjectSyncNginxServerPortOptionsResponse(data=data)


@router.post('/sync/check-nginx-server-block', name='同步项目检查Nginx server块', response_model=schemas.pspm.ProjectSyncNginxServerBlockCheckResponse)
async def check_sync_nginx_server_block(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'create')),
  payload: schemas.pspm.ProjectSyncNginxServerBlockCheckRequest,
):
  """检查同步已有项目的 Nginx 配置文件中是否存在匹配端口的 server 块。"""
  data = await check_sync_nginx_server_block_service(session, current_user, payload)
  return schemas.pspm.ProjectSyncNginxServerBlockCheckResponse(data=data)


@router.post('/sync', name='同步已有项目', response_model=schemas.pspm.ProjectSyncResponse)
async def sync_existing_project(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'create')),
  payload: schemas.pspm.ProjectSyncRequest,
):
  """同步已经存在的项目到系统。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要项目创建权限。
  - payload：项目目录、Conda、可选数据库和可选 Nginx 配置。

  作用：
  - 只登记已存在资源，不创建目录、不创建 Conda、不创建数据库、不写 Nginx 配置文件。
  - 每一项已选资源都会在后端再次检测是否存在、是否可用。

  返回：
  - ProjectSyncResponse，包含新登记项目 ID 和关键配置。
  """
  data = await sync_existing_project_service(session, current_user, payload)
  return schemas.pspm.ProjectSyncResponse(message='同步成功', data=data)


@router.post('/create-real', name='真实创建项目', response_model=schemas.pspm.ProjectRealCreateResponse)
async def create_project_real(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'create')),
  payload: schemas.pspm.ProjectRealCreateRequest,
):
  """真实创建项目。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要创建项目权限。
  - payload：新建项目弹框提交的完整配置，包括服务器、路径、Python、Conda、数据库、Nginx。

  作用：
  - 真实创建项目目录、Conda 环境、可选数据库、可选 Nginx server block。
  - 最后写入项目表记录。
  - 任何步骤失败都会尽量回滚已完成动作。

  返回：
  - `ProjectRealCreateResponse`，包含创建后的项目 ID、路径、Conda 环境和执行日志。
  """
  data = await create_project_real_service(session, current_user, payload)
  return schemas.pspm.ProjectRealCreateResponse(message='创建成功', data=data)


@router.post('/create', name='创建', response_model=schemas.base.BaseResponse)
async def create_project(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'create')),
  payload: schemas.pspm.ProjectCreate,
):
  """创建项目基础记录。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要创建项目权限。
  - payload：项目基础字段。

  作用：
  - 兼容旧版只写数据库记录的创建接口。
  - 不真实创建目录、Conda、数据库或 Nginx 配置。

  返回：
  - `BaseResponse`，message 为创建成功。
  """
  await create_project_record_service(session, current_user, payload)
  return schemas.base.BaseResponse(message='创建成功')


@router.put('/setting', name='设置', response_model=schemas.base.BaseResponse)
async def update_project_setting(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'setting')),
  project_id: int = Query(..., description='项目ID'),
  payload: schemas.pspm.ProjectSettingUpdate,
):
  """保存项目设置。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要项目设置权限。
  - project_id：项目 ID，来自 Query。
  - payload：设置弹框最终提交的配置。

  作用：
  - 保存项目描述、Conda、入口文件、启动命令、数据库、Nginx 等配置。
  - 根据前端危险确认结果，真实创建/删除 Conda、数据库或 Nginx 配置。

  返回：
  - `BaseResponse`，message 为设置保存成功。
  """
  data = await update_project_setting_service(session, current_user, project_id, payload)
  return schemas.base.ItemResponse(message='设置保存成功', data=data)


@router.delete('/database/original', name='删除原数据库', response_model=schemas.base.BaseResponse)
async def delete_original_project_database(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'setting')),
  project_id: int = Query(..., description='项目ID'),
):
  """删除项目原数据库并清空项目表数据库字段。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要项目设置权限。
  - project_id：项目 ID，来自 Query。

  作用：
  - 给前端独立删除原数据库使用。
  - 删除完成后同步清空项目表中的数据库连接信息。

  返回：
  - `BaseResponse`，message 为原数据库已删除。
  """
  await delete_original_project_database_service(session, current_user, project_id)
  return schemas.base.BaseResponse(message='原数据库已删除')


@router.put('/start-foreground', name='前台启动', response_model=schemas.base.BaseResponse)
async def start_foreground(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'start_foreground')),
  project_id: int = Query(..., description='项目ID'),
):
  """前台启动项目。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要前台启动权限。
  - project_id：项目 ID。

  作用：
  - 使用项目设置中的开发启动命令启动项目。
  - 启动成功后把项目状态更新为运行中。

  返回：
  - `BaseResponse`，message 为启动结果。
  """
  message = await start_project_service(session, current_user, project_id, mode='dev', run_in_background=False)
  return schemas.base.BaseResponse(message=message or '前台启动成功')


@router.put('/start-background', name='后台启动', response_model=schemas.base.BaseResponse)
async def start_background(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'start_background')),
  project_id: int = Query(..., description='项目ID'),
):
  """后台启动项目。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要后台启动权限。
  - project_id：项目 ID。

  作用：
  - 使用项目设置中的开发启动命令后台启动项目。
  - 启动成功后把项目状态更新为运行中。

  返回：
  - `BaseResponse`，message 为启动结果。
  """
  message = await start_project_service(session, current_user, project_id, mode='dev', run_in_background=True)
  return schemas.base.BaseResponse(message=message or '后台启动成功')


@router.put('/deploy-start', name='部署启动', response_model=schemas.base.BaseResponse)
async def deploy_start(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'deploy_start')),
  project_id: int = Query(..., description='项目ID'),
):
  """部署启动项目。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要部署启动权限。
  - project_id：项目 ID。

  作用：
  - 使用项目设置中的部署启动命令后台启动项目。
  - 启动成功后把项目状态更新为运行中。

  返回：
  - `BaseResponse`，message 为启动结果。
  """
  message = await start_project_service(session, current_user, project_id, mode='deploy', run_in_background=True)
  return schemas.base.BaseResponse(message=message or '部署启动成功')


@router.put('/stop', name='停止服务', response_model=schemas.base.BaseResponse)
async def stop_project(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'stop')),
  project_id: int = Query(..., description='项目ID'),
):
  """停止项目服务。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要停止服务权限。
  - project_id：项目 ID。

  作用：
  - 只停止当前项目 runtime 元数据记录的 PID，避免误杀其他进程。
  - 停止成功后把项目状态更新为已停止。

  返回：
  - `BaseResponse`，message 为停止结果。
  """
  message = await stop_project_service(session, current_user, project_id)
  return schemas.base.BaseResponse(message=message or '停止服务成功')


@router.post('/copy', name='复制', response_model=schemas.base.BaseResponse)
async def copy_project(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'copy')),
  project_id: int = Query(..., description='项目ID'),
  payload: schemas.pspm.ProjectCopyRequest,
):
  """复制项目。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要复制权限。
  - project_id：项目 ID。
  - payload：目标服务器和目标目录。

  作用：
  - 当前版本只做权限校验并返回任务提示。
  - 后续真实复制逻辑会放在 service 层，不改接口层。

  返回：
  - `BaseResponse`，message 为复制任务提示。
  """
  message = await copy_project_service(session, current_user, project_id, payload.target_server_ip, payload.target_dir)
  return schemas.base.BaseResponse(message=message)


@router.post('/export', name='导出', response_model=schemas.base.BaseResponse)
async def export_project(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'export')),
  project_id: int = Query(..., description='项目ID'),
  payload: schemas.pspm.ProjectExportRequest,
):
  """导出项目。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要导出权限。
  - project_id：项目 ID。
  - payload：导出目录。

  作用：
  - 当前版本只做权限校验并返回任务提示。
  - 后续真实导出逻辑会放在 service 层，不改接口层。

  返回：
  - `BaseResponse`，message 为导出任务提示。
  """
  message = await export_project_service(session, current_user, project_id, payload.target_dir)
  return schemas.base.BaseResponse(message=message)


@router.delete('/delete', name='删除', response_model=schemas.base.BaseResponse)
async def delete_project(
  *,
  session: SessionDep,
  current_user=Depends(require_permission('project_management', 'delete')),
  id: List[int] = Query(..., description='项目ID列表'),
  delete_scope: schemas.pspm.ProjectDeleteScope = Query(DELETE_SCOPE_PROJECT_ONLY, description='删除范围'),
):
  """删除项目及关联资源。

  参数：
  - session：数据库会话。
  - current_user：当前登录用户，需要删除权限。
  - id：项目 ID 列表，来自 Query，可一次删除多个。
  - delete_scope：删除范围，可选只删项目、项目+Conda、项目+Conda+数据库、项目+Conda+数据库+Nginx。

  作用：
  - 根据用户选择删除项目目录、Conda 环境、数据库和 Nginx server block。
  - 最后软删除项目表记录。

  返回：
  - `BaseResponse`，message 为删除成功和删除范围。
  """
  message = await delete_project_service(session, current_user, id, delete_scope)
  return schemas.base.BaseResponse(message=message)
