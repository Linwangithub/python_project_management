"""项目管理路由模块，包含创建、设置、删除数据库和删除项目。

本模块从项目路由聚合入口拆分而来，只维护同一类项目 HTTP 路由。
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Query

from app import schemas
from app.api.deps import require_permission
from app.core.deps import SessionDep

router = APIRouter()

from app.services.pspm.project_create import create_project_real_service, create_project_record_service
from app.services.pspm.project_delete import delete_project_service
from app.services.pspm.project_setting import (
  delete_original_project_database_service,
  update_project_setting_service,
)
from app.utils.pspm.project_api_messages import (
  MSG_PROJECT_CREATE_SUCCESS,
  MSG_PROJECT_ORIGINAL_DATABASE_DELETED,
  MSG_PROJECT_SETTING_SUCCESS,
)
from app.utils.pspm.project_config import DELETE_SCOPE_PROJECT_ONLY

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
  return schemas.pspm.ProjectRealCreateResponse(message=MSG_PROJECT_CREATE_SUCCESS, data=data)


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
  return schemas.base.BaseResponse(message=MSG_PROJECT_CREATE_SUCCESS)


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
  return schemas.base.ItemResponse(message=MSG_PROJECT_SETTING_SUCCESS, data=data)


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
  return schemas.base.BaseResponse(message=MSG_PROJECT_ORIGINAL_DATABASE_DELETED)


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
