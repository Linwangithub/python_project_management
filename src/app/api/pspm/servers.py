"""服务器接口模块，处理服务器资产、连通性和用户目录相关请求。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from __future__ import annotations

import shlex
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query

from app import crud, schemas
from app.api.deps import require_permission
from app.services.pspm.server_validation import (
  _safe_root_password,
  _safe_server_ip,
  _safe_username,
  _verify_root_password,
  _verify_server_ping,
  _run_shell,
)
from app.core.deps import SessionDep

router = APIRouter()

LINUX_USER_CREATE_TIMEOUT = 20
LINUX_USER_DELETE_TIMEOUT = 20
SERVER_PING_TIMEOUT = 4
SERVER_AUTH_TIMEOUT = 12


@router.get('', name='列表', response_model=schemas.pspm.ServerItemsResponse)
async def list_servers(
    *,
    session: SessionDep,
    current_user=Depends(require_permission('server_management', None)),
    page: int = Query(1, description='页码'),
    page_size: int = Query(20, description='每页数量'),
):
    """查询服务器管理列表。

    参数：
    - session：数据库会话。
    - current_user：当前登录用户，已通过服务器管理菜单权限校验。
    - page：页码。
    - page_size：每页数量。

    返回：
    - ServerItemsResponse：包含服务器总数和服务器行数据。
    """
    is_root = await crud.rbac.is_root_user(session, user_id=current_user.id)
    result = await crud.servers.get_items(
        session,
        user_id=current_user.id,
        is_root=is_root,
        page=page,
        page_size=page_size,
    )
    return schemas.pspm.ServerItemsResponse(data=result)


@router.post('/create', name='创建', response_model=schemas.base.BaseResponse)
async def create_server(
    *,
    session: SessionDep,
    current_user=Depends(require_permission('server_management', 'create')),
    payload: schemas.pspm.ServerCreate,
):
    """新增服务器记录并验证服务器可达性和 root 密码。

    参数：
    - session：数据库会话。
    - current_user：当前登录用户，已通过新增服务器权限校验。
    - payload：服务器 IP、root 密码、备注等前端表单数据。

    返回：
    - BaseResponse：创建成功提示。
    """
    ip = _safe_server_ip(payload.ip)
    root_password = _safe_root_password(payload.root_password)

    exists = await crud.servers.get(session, obj_in={'ip': ip, 'ssh_port': 22, 'status': 1})
    if exists:
        raise HTTPException(status_code=400, detail='服务器已存在')

    ping_ok, ping_msg = await _verify_server_ping(ip, timeout=SERVER_PING_TIMEOUT)
    if not ping_ok:
        raise HTTPException(status_code=400, detail=ping_msg)

    auth_ok, auth_msg = await _verify_root_password(ip, root_password, timeout=SERVER_AUTH_TIMEOUT)
    if not auth_ok:
        raise HTTPException(status_code=400, detail=auth_msg)

    await crud.servers.create(
        session,
        obj_in={
            'alias': payload.alias,
            'ip': ip,
            'ssh_port': 22,
            'root_password': root_password,
            'assigned_users': 'root',
            'middlewares': None,
            'remark': payload.remark,
            'created_by': current_user.id,
            'status': 1,
        },
    )
    return schemas.base.BaseResponse(message='创建成功')


@router.delete('/delete', name='删除', response_model=schemas.base.BaseResponse)
async def delete_server(
    *,
    session: SessionDep,
    current_user=Depends(require_permission('server_management', 'delete')),
    id: List[int] = Query(..., description='服务器ID列表'),
):
    """删除服务器表中的记录。

    参数：
    - session：数据库会话。
    - current_user：当前登录用户，已通过删除服务器权限校验。
    - id：待删除服务器 ID 列表。

    返回：
    - BaseResponse：删除成功提示。
    """
    if not id:
        raise HTTPException(status_code=400, detail='缺少服务器ID')

    rows = await crud.servers.remove_multi(session, ids=id)
    if rows <= 0:
        raise HTTPException(status_code=400, detail='删除失败')

    return schemas.base.BaseResponse(message='删除成功')


@router.post('/users/create', name='增加用户', response_model=schemas.base.BaseResponse)
async def create_server_user(
    *,
    session: SessionDep,
    current_user=Depends(require_permission('server_management', 'assign')),
    payload: schemas.pspm.ServerUserCreate,
):
    """在当前后端所在服务器创建 Linux 用户，并写入服务器分配用户列表。

    参数：
    - session：数据库会话。
    - current_user：当前登录用户，已通过服务器分配权限校验。
    - payload：服务器 ID 和待创建的 Linux 用户名。

    返回：
    - BaseResponse：增加用户成功提示。
    """
    server = await crud.servers.get(session, obj_in={'id': payload.server_id, 'status': 1})
    if not server:
        raise HTTPException(status_code=404, detail='服务器不存在')

    username = _safe_username(payload.username)
    if username == 'root':
        raise HTTPException(status_code=400, detail='root用户默认存在，无需重复创建')

    check_cmd = f'id -u {shlex.quote(username)} >/dev/null 2>&1'
    check_code, _, _ = await _run_shell(check_cmd, timeout=LINUX_USER_CREATE_TIMEOUT)
    if check_code == 0:
        raise HTTPException(status_code=400, detail='该系统用户已存在')

    create_cmd = f'useradd -m {shlex.quote(username)}'
    code, _, err = await _run_shell(create_cmd, timeout=LINUX_USER_CREATE_TIMEOUT)
    if code != 0:
        raise HTTPException(status_code=500, detail=f'创建系统用户失败：{err or '未知错误'}')

    await crud.servers.add_assigned_user(session, server_id=payload.server_id, username=username)
    return schemas.base.BaseResponse(message='增加用户成功')


@router.post('/users/delete', name='删除用户', response_model=schemas.base.BaseResponse)
async def delete_server_user(
    *,
    session: SessionDep,
    current_user=Depends(require_permission('server_management', 'delete')),
    payload: schemas.pspm.ServerUserDelete,
):
    """删除服务器上的普通 Linux 用户，并同步移出分配用户列表。

    参数：
    - session：数据库会话。
    - current_user：当前登录用户，已通过删除权限校验。
    - payload：服务器 ID 和待删除的 Linux 用户名。

    返回：
    - BaseResponse：删除用户成功提示。
    """
    server = await crud.servers.get(session, obj_in={'id': payload.server_id, 'status': 1})
    if not server:
        raise HTTPException(status_code=404, detail='服务器不存在')

    username = _safe_username(payload.username)
    if username == 'root':
        raise HTTPException(status_code=400, detail='禁止删除root用户')

    check_cmd = f'id -u {shlex.quote(username)} >/dev/null 2>&1'
    check_code, _, _ = await _run_shell(check_cmd, timeout=LINUX_USER_DELETE_TIMEOUT)
    if check_code != 0:
        raise HTTPException(status_code=404, detail='系统用户不存在')

    del_cmd = f'userdel -r {shlex.quote(username)}'
    del_code, _, del_err = await _run_shell(del_cmd, timeout=LINUX_USER_DELETE_TIMEOUT)
    if del_code != 0:
        raise HTTPException(status_code=500, detail=f'删除系统用户失败：{del_err or '未知错误'}')

    await crud.servers.remove_assigned_user(session, server_id=payload.server_id, username=username)
    return schemas.base.BaseResponse(message='删除用户成功')
