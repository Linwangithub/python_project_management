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
    data = await crud.rbac.get_user_permission_snapshot(session, user=current_user)
    return schemas.rbac.UserPermissionResponse(data=data)
