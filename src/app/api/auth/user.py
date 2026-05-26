"""当前用户接口模块，提供登录用户信息和权限快照查询。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

import logging

from fastapi import APIRouter, Body, HTTPException

from app import crud, schemas
from app.api.deps import CurrentUser
from app.core.deps import RedisDep, SessionDep, get_settings
from app.utils.pspm.project_config import ROOT_PROJECT_BASE_DIR, USER_PROJECT_BASE_PATH_TEMPLATE

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/me", name="个人信息详情", response_model=schemas.users.ItemResponse)
async def read_user_me(
    *,
    session: SessionDep,
    redis: RedisDep,
    current_user: CurrentUser,
) -> schemas.users.ItemResponse:
    """查询当前登录用户信息。

    返回用户基础信息以及当前角色可用的项目目录前缀配置。
    """
    settings = get_settings()
    project_root_base_path = str(settings.project_paths.root_base_path or ROOT_PROJECT_BASE_DIR).strip()
    project_user_template = str(settings.project_paths.user_base_path_template or USER_PROJECT_BASE_PATH_TEMPLATE).strip()

    username = str(current_user.username or '').strip() or 'user'
    is_root = await crud.rbac.is_root_user(session, user_id=current_user.id)
    if is_root:
        project_base_path = project_root_base_path
    else:
        project_base_path = project_user_template.replace('{username}', username)

    user_payload = current_user.model_dump()
    user_payload.update({
        'project_base_path': project_base_path,
        'project_root_base_path': project_root_base_path,
        'project_user_base_path_template': project_user_template,
    })
    return schemas.users.ItemResponse(data=schemas.users.Item(**user_payload))


@router.put("/password", name="修改密码", response_model=schemas.base.BaseResponse)
async def update_user_password(
    *,
    session: SessionDep,
    redis: RedisDep,
    current_user: CurrentUser,
    old_password: str = Body(..., description="旧密码"),
    password: str = Body(..., description="新密码"),
    password_confirmation: str = Body(..., description="确认密码"),
) -> schemas.base.BaseResponse:
    """修改当前登录用户密码。

    校验旧密码和确认密码后更新密码哈希。
    """
    if password != password_confirmation:
        raise HTTPException(status_code=400, detail="密码与确认密码不一致")
    if not await crud.users.authenticate(session, username=current_user.username, password=old_password):
        raise HTTPException(status_code=400, detail="旧密码错误")

    await crud.users.update(session, obj_in={"id": current_user.id}, data_in={"password": password})
    return schemas.base.BaseResponse()
