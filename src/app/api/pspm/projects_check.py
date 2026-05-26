"""项目即时校验路由模块，包含项目名、数据库、Nginx 和端口校验。

本模块从项目路由聚合入口拆分而来，只维护同一类项目 HTTP 路由。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app import schemas
from app.api.deps import require_permission
from app.core.deps import SessionDep

router = APIRouter()

from app.services.pspm.project_checks import (
  check_project_database_service,
  check_project_name_service,
  check_project_nginx_service,
  check_project_port_service,
)

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
  - base_path：项目基础路径，例如“项目基础路径配置值”。
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
