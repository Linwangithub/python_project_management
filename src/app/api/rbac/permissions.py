"""权限接口模块，处理菜单权限查询和权限配置管理。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from fastapi import APIRouter

from app import crud, schemas
from app.api.deps import CurrentUser
from app.core.deps import SessionDep

router = APIRouter()


@router.get('/me', name='我的权限', response_model=schemas.rbac.UserPermissionResponse)
async def my_permissions(
    *,
    session: SessionDep,
    current_user: CurrentUser,
):
    """查询当前登录用户的权限快照。

    前端根据返回结果控制菜单和按钮显示。
    """
    data = await crud.rbac.get_user_permission_snapshot(session, user=current_user)
    return schemas.rbac.UserPermissionResponse(data=data)
