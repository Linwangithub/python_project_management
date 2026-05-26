"""项目同步路由模块，包含同步已有项目的目录、Conda、数据库和 Nginx 检查。

本模块从项目路由聚合入口拆分而来，只维护同一类项目 HTTP 路由。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app import schemas
from app.api.deps import require_permission
from app.core.deps import SessionDep

router = APIRouter()

from app.services.pspm.project_sync_nginx import (
  check_sync_nginx_server_block_service,
  list_sync_nginx_server_port_options_service,
)
from app.services.pspm.project_sync import (
  check_sync_conda_service,
  check_sync_database_service,
  list_sync_conda_envs_service,
  list_sync_entry_path_children_service,
  list_sync_project_path_children_service,
  sync_existing_project_service,
)
from app.utils.pspm.project_api_messages import MSG_PROJECT_SYNC_SUCCESS

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
  - 后端强制目录位于 项目路径配置前缀下。

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
  return schemas.pspm.ProjectSyncResponse(message=MSG_PROJECT_SYNC_SUCCESS, data=data)
