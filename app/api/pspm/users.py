from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import update

from app import crud, models, schemas
from app.api.deps import require_permission
from app.core.deps import SessionDep

router = APIRouter()


@router.get('', name='列表', response_model=schemas.pspm.UserItemsResponse)
async def list_users(
    *,
    session: SessionDep,
    current_user = Depends(require_permission('user_management', None)),
    page: int = Query(1, description='页码'),
    page_size: int = Query(20, description='每页数量'),
):
    """查询用户管理列表。

    参数：
    - session：数据库会话，由 FastAPI 依赖注入。
    - current_user：当前登录用户，已通过用户管理菜单权限校验。
    - page：页码，来自 Query 参数。
    - page_size：每页数量，来自 Query 参数。

    返回：
    - UserItemsResponse：包含用户总数和用户行数据。
    """
    result = await crud.users.get_multi(session, obj_in={}, page=page, page_size=page_size)
    op_map = await crud.pspm.get_user_name_map(session)

    rows = []
    for item in result.data:
        role_keys = await crud.rbac.get_user_role_keys(session, user_id=item.id)
        role_name = crud.pspm.role_keys_to_name(role_keys)
        rows.append(
            schemas.pspm.UserItem(
                id=item.id,
                userid=item.userid,
                username=item.username,
                password=item.password,
                role=role_name,
                operator=op_map.get(current_user.id, 'system'),
                created_at=item.created_at,
            )
        )

    return schemas.pspm.UserItemsResponse(data=schemas.pspm.UserItems(total=result.total, data=rows))


@router.post('/create', name='创建', response_model=schemas.base.BaseResponse)
async def create_user(
    *,
    session: SessionDep,
    current_user = Depends(require_permission('user_management', 'create')),
    payload: schemas.pspm.UserCreate,
):
    """创建系统账号并绑定角色。

    参数：
    - session：数据库会话。
    - current_user：当前登录用户，已通过创建用户权限校验。
    - payload：前端提交的账号、明文密码和角色名称。

    返回：
    - BaseResponse：创建成功提示。
    """
    exists = await crud.users.get(session, obj_in={'username': payload.username})
    if exists:
        raise HTTPException(status_code=400, detail='账号已存在')

    all_rows = await crud.users.get_multi(session, obj_in={}, page=1, page_size=100000, page_break=True)
    next_userid = 1
    if all_rows.data:
        next_userid = max([x.userid for x in all_rows.data]) + 1

    user = await crud.users.create(
        session,
        obj_in={
            'userid': next_userid,
            'username': payload.username,
            'password': payload.password,
        },
    )

    role_key = 'root' if payload.role == 'root' else 'user'
    role = await crud.rbac.get_role_by_key(session, role_key=role_key)
    if not role:
        raise HTTPException(status_code=500, detail='角色不存在，请先初始化RBAC数据')
    await crud.rbac.bind_user_role(session, user_id=user.id, role_id=role.id)

    return schemas.base.BaseResponse(message='创建成功')


@router.delete('/delete', name='删除', response_model=schemas.base.BaseResponse)
async def delete_user(
    *,
    session: SessionDep,
    current_user = Depends(require_permission('user_management', 'delete')),
    id: List[int] = Query(..., description='用户ID列表'),
    transfer_projects: bool = Query(True, description='是否迁移项目到当前用户目录'),
):
    """删除用户并可选迁移其项目归属。

    参数：
    - session：数据库会话。
    - current_user：当前登录用户，已通过删除用户权限校验。
    - id：待删除用户 ID 列表，来自 Query 参数。
    - transfer_projects：是否把被删除用户的项目转移给当前用户。

    返回：
    - BaseResponse：删除成功提示。
    """
    ids = [uid for uid in id if uid != current_user.id]
    if not ids:
        raise HTTPException(status_code=400, detail='没有可删除的用户')

    for uid in ids:
        role_keys = await crud.rbac.get_user_role_keys(session, user_id=uid)
        if 'root' in role_keys:
            raise HTTPException(status_code=400, detail='不能删除root用户')

    if transfer_projects:
        await crud.projects.transfer_owner(session, from_user_ids=ids, to_user_id=current_user.id)

    await crud.users.remove(session, obj_in={'id': ids})
    await session.execute(
        update(models.pspm.PspmServerUser)
        .where(models.pspm.PspmServerUser.user_id.in_(ids), models.pspm.PspmServerUser.status != -1)
        .values(status=-1)
    )
    await session.execute(
        update(models.rbac.RbacUserRole)
        .where(models.rbac.RbacUserRole.user_id.in_(ids), models.rbac.RbacUserRole.status != -1)
        .values(status=-1)
    )
    await session.commit()
    return schemas.base.BaseResponse(message='删除成功')
