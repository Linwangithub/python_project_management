"""用户权限接口模块，处理用户列表、用户创建和权限相关操作。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from typing import Any, List

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app import crud, schemas
from app.api.deps import require_permission
from app.core.deps import SessionDep

router = APIRouter()


@router.get('', name='列表', response_model=schemas.users.ItemsResponse)
async def _index_(
    *,
    session: SessionDep,
    current_user = Depends(require_permission('user_management', None)),
    page: int = Query(1, description='页码'),
    page_size: int = Query(20, description='每页数量'),
) -> schemas.users.ItemsResponse:
    """分页查询用户列表接口。

    支持用户管理页面加载用户数据。
    """
    obj_in = {}
    result = await crud.users.get_multi(session, obj_in=obj_in, page=page, page_size=page_size)

    return schemas.users.ItemsResponse(
        data=schemas.users.Items(
            total=result.total,
            data=result.data,
        ),
    )


@router.get('/view', name='详情', response_model=schemas.users.ItemResponse)
async def _view_(
    *,
    session: SessionDep,
    current_user = Depends(require_permission('user_management', None)),
    id: int = Query(..., description='用户ID'),
) -> Any:
    """查询单个用户详情接口。"""
    obj_in = {'id': id}
    result = await crud.users.get(session, obj_in)
    if result:
        return schemas.users.ItemResponse(data=result)
    raise HTTPException(status_code=400, detail='未知用户')


@router.post('/create', name='添加', response_model=schemas.base.BaseResponse)
async def _create_(
    *,
    session: SessionDep,
    current_user = Depends(require_permission('user_management', 'create')),
    username: str = Body(..., description='用户名'),
    password: str = Body(..., description='密码'),
) -> Any:
    """创建用户接口。

    写入用户基础信息并绑定角色。
    """
    if await crud.users.get(session, obj_in={'username': username}):
        raise HTTPException(status_code=400, detail='该用户已存在，请重新填写用户名称')

    all_rows = await crud.users.get_multi(session, obj_in={}, page=1, page_size=100000, page_break=True)
    next_userid = 1
    if all_rows.data:
        next_userid = max([x.userid for x in all_rows.data]) + 1

    result = await crud.users.create(
        session,
        obj_in={
            'userid': next_userid,
            'username': username,
            'password': password,
        },
    )
    if result:
        role = await crud.rbac.get_role_by_key(session, role_key='user')
        if role:
            await crud.rbac.bind_user_role(session, user_id=result.id, role_id=role.id)
        return schemas.base.BaseResponse()
    raise HTTPException(status_code=400, detail='添加失败，请重试')


@router.put('/update', name='更新', response_model=schemas.base.BaseResponse)
async def _update_(
    *,
    session: SessionDep,
    current_user = Depends(require_permission('user_management', 'update')),
    id: int = Query(..., description='用户ID'),
    username: str = Body(..., description='用户名'),
) -> Any:
    """更新用户基础信息接口。"""
    result = await crud.users.get(session, obj_in={'id': id})
    if not result:
        raise HTTPException(status_code=400, detail='用户不存在')

    verify_user = await crud.users.get(session, obj_in={'username': username})
    if verify_user and (verify_user.id != result.id):
        raise HTTPException(status_code=400, detail='用户名已存在，请重新填写')

    rows = await crud.users.update(session, obj_in={'id': id}, data_in={'username': username})
    if rows:
        return schemas.base.BaseResponse()
    raise HTTPException(status_code=400, detail='更新失败，请重试')


@router.put('/update-password', name='更新密码', response_model=schemas.base.BaseResponse)
async def _update_password_(
    *,
    session: SessionDep,
    current_user = Depends(require_permission('user_management', 'update_password')),
    id: int = Query(..., description='用户ID'),
    password: str = Body(..., description='密码'),
    password_confirmation: str = Body(..., description='确认密码'),
) -> Any:
    """管理员重置用户密码接口。"""
    if password != password_confirmation:
        raise HTTPException(status_code=400, detail='密码与确认密码不一致')
    result = await crud.users.update(session, obj_in={'id': id}, data_in={'password': password})
    if result:
        return schemas.base.BaseResponse()
    raise HTTPException(status_code=400, detail='更新失败，请重试')


@router.delete('/delete', name='删除', response_model=schemas.base.BaseResponse)
async def _delete_(
    *,
    session: SessionDep,
    current_user = Depends(require_permission('user_management', 'delete')),
    id: List[int] = Query(..., description='用户ID'),
) -> Any:
    """删除用户接口。"""
    obj_in = {'id': id}
    result = await crud.users.remove(session, obj_in=obj_in)
    if result:
        return schemas.base.BaseResponse()
    raise HTTPException(status_code=400, detail='删除失败，请重试')
