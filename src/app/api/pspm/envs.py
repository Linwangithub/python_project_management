from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query

from app import crud, schemas
from app.api.deps import require_permission
from app.core.deps import SessionDep

router = APIRouter()


@router.get('', name='列表', response_model=schemas.pspm.EnvItemsResponse)
async def list_envs(
    *,
    session: SessionDep,
    current_user = Depends(require_permission('env_management', None)),
    page: int = Query(1, description='页码'),
    page_size: int = Query(20, description='每页数量'),
):
    """查询当前用户可见的 Conda 环境列表。

    参数：
    - session：数据库会话，由 FastAPI 依赖注入。
    - current_user：当前登录用户，已通过环境管理菜单权限校验。
    - page：页码，来自 Query 参数。
    - page_size：每页数量，来自 Query 参数。

    返回：
    - EnvItemsResponse：包含环境总数和环境行数据。
    """
    is_root = await crud.rbac.is_root_user(session, user_id=current_user.id)
    owner_id = None if is_root else current_user.id
    result = await crud.envs.get_items(session, owner_id=owner_id, page=page, page_size=page_size)
    return schemas.pspm.EnvItemsResponse(data=result)


@router.post('/create', name='创建', response_model=schemas.base.BaseResponse)
async def create_env(
    *,
    session: SessionDep,
    current_user = Depends(require_permission('env_management', 'create')),
    payload: schemas.pspm.EnvCreate,
):
    """创建环境管理中的环境记录。

    参数：
    - session：数据库会话。
    - current_user：当前登录用户，已通过创建环境权限校验。
    - payload：前端提交的环境名称、关联项目、Python 版本和主包信息。

    返回：
    - BaseResponse：创建成功提示。
    """
    exists = await crud.envs.get(session, obj_in={'owner_id': current_user.id, 'env_name': payload.env_name, 'status': 1})
    if exists:
        raise HTTPException(status_code=400, detail='环境名称已存在')

    await crud.envs.create(
        session,
        obj_in={
            'owner_id': current_user.id,
            'env_name': payload.env_name,
            'project_name': payload.project_name,
            'python_version': payload.python_version,
            'main_packages': payload.main_packages,
            'created_by': current_user.id,
            'status': 1,
        },
    )
    return schemas.base.BaseResponse(message='创建成功')


@router.delete('/delete', name='删除', response_model=schemas.base.BaseResponse)
async def delete_env(
    *,
    session: SessionDep,
    current_user = Depends(require_permission('env_management', 'delete')),
    id: List[int] = Query(..., description='环境ID列表'),
):
    """批量删除环境记录。

    参数：
    - session：数据库会话。
    - current_user：当前登录用户，已通过删除环境权限校验。
    - id：待删除环境 ID 列表，来自 Query 参数。

    返回：
    - BaseResponse：删除成功提示。
    """
    rows = await crud.envs.remove_multi(session, ids=id)
    if rows <= 0:
        raise HTTPException(status_code=400, detail='删除失败')
    return schemas.base.BaseResponse(message='删除成功')
