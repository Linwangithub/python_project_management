from __future__ import annotations

import asyncio
import os
import pty
import select
import shlex
import time
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query

from app import crud, schemas
from app.api.deps import require_permission
from app.core.deps import SessionDep

router = APIRouter()

LINUX_USER_CREATE_TIMEOUT = 20
LINUX_USER_DELETE_TIMEOUT = 20
SERVER_PING_TIMEOUT = 4
SERVER_AUTH_TIMEOUT = 12


def _safe_username(username: str) -> str:
    """校验并清洗 Linux 用户名。

    参数：
    - username：前端提交的用户名。

    返回：
    - str：去掉首尾空格后的安全用户名。
    """
    value = (username or '').strip()
    if not value:
        raise HTTPException(status_code=400, detail='用户名不能为空')
    if not crud.pspm.is_valid_linux_username(value):
        raise HTTPException(
            status_code=400,
            detail='用户名不合法，仅支持小写字母/数字/_/-，且必须以字母或下划线开头',
        )
    return value


def _safe_server_ip(ip: str) -> str:
    """校验并清洗服务器 IP。

    参数：
    - ip：前端提交的服务器 IP。

    返回：
    - str：去掉首尾空格后的服务器 IP。
    """
    value = (ip or '').strip()
    if not value:
        raise HTTPException(status_code=400, detail='服务器IP不能为空')
    if ' ' in value:
        raise HTTPException(status_code=400, detail='服务器IP格式不合法')
    return value


def _safe_root_password(password: str | None) -> str:
    """校验 root 密码是否为空。

    参数：
    - password：前端提交的 root 明文密码。

    返回：
    - str：去掉首尾空格后的 root 密码。
    """
    value = (password or '').strip()
    if not value:
        raise HTTPException(status_code=400, detail='root密码不能为空')
    return value


async def _run_shell(command: str, timeout: int = 20) -> tuple[int, str, str]:
    """执行本机 shell 命令并返回退出码、标准输出和错误输出。

    参数：
    - command：需要通过 bash 执行的命令字符串。
    - timeout：命令超时时间，单位为秒。

    返回：
    - tuple[int, str, str]：退出码、stdout、stderr。
    """
    proc = await asyncio.create_subprocess_exec(
        '/bin/bash',
        '-lc',
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return 124, '', f'命令执行超时（>{timeout}s）'

    stdout = (stdout_b or b'').decode('utf-8', errors='replace').strip()
    stderr = (stderr_b or b'').decode('utf-8', errors='replace').strip()
    return int(proc.returncode or 0), stdout, stderr


async def _verify_server_ping(ip: str, timeout: int = SERVER_PING_TIMEOUT) -> tuple[bool, str]:
    """通过 ping 检查服务器网络是否可达。

    参数：
    - ip：目标服务器 IP。
    - timeout：ping 超时时间，单位为秒。

    返回：
    - tuple[bool, str]：是否可达和提示信息。
    """
    cmd = f'ping -c 1 -W {int(timeout)} {shlex.quote(ip)}'
    code, out, err = await _run_shell(cmd, timeout=timeout + 2)
    if code == 0:
        return True, 'PING可达'
    msg = (err or out or '').strip()
    if not msg:
        msg = 'PING不可达'
    return False, f'PING失败：{msg}'


def _verify_root_password_blocking(ip: str, password: str, timeout: int = SERVER_AUTH_TIMEOUT) -> tuple[bool, str]:
    """阻塞式验证目标服务器 root 密码是否可用。

    参数：
    - ip：目标服务器 IP。
    - password：root 明文密码。
    - timeout：SSH 认证超时时间，单位为秒。

    返回：
    - tuple[bool, str]：认证是否成功和提示信息。
    """
    # 仅做认证，不进入交互会话
    argv = [
        'ssh',
        '-o', 'PreferredAuthentications=password,keyboard-interactive',
        '-o', 'PubkeyAuthentication=no',
        '-o', 'PasswordAuthentication=yes',
        '-o', 'KbdInteractiveAuthentication=yes',
        '-o', 'NumberOfPasswordPrompts=1',
        '-o', 'StrictHostKeyChecking=no',
        '-o', 'UserKnownHostsFile=/dev/null',
        '-o', f'ConnectTimeout={int(timeout)}',
        f'root@{ip}',
        'exit',
    ]

    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.execvp(argv[0], argv)
        except Exception:
            os._exit(127)

    sent_password = False
    output_parts: list[str] = []
    deadline = time.monotonic() + timeout + 2

    try:
        while True:
            now = time.monotonic()
            if now >= deadline:
                try:
                    os.kill(pid, 9)
                except Exception:
                    pass
                try:
                    os.waitpid(pid, 0)
                except Exception:
                    pass
                return False, 'SSH连接失败：连接超时'

            rlist, _, _ = select.select([fd], [], [], 0.4)
            if not rlist:
                done_pid, _ = os.waitpid(pid, os.WNOHANG)
                if done_pid == pid:
                    break
                continue

            try:
                data = os.read(fd, 4096)
            except OSError:
                break
            if not data:
                break

            chunk = data.decode('utf-8', errors='replace')
            output_parts.append(chunk)

            lower_all = ''.join(output_parts).lower()
            if 'are you sure you want to continue connecting' in lower_all:
                try:
                    os.write(fd, b'yes\n')
                except OSError:
                    pass
            if (not sent_password) and ("password:" in lower_all or "password for" in lower_all):
                try:
                    os.write(fd, (password + '\n').encode('utf-8'))
                    sent_password = True
                except OSError:
                    pass

        status = 1
        try:
            waited_pid, waited_status = os.waitpid(pid, 0)
            if waited_pid == pid:
                if os.WIFEXITED(waited_status):
                    status = os.WEXITSTATUS(waited_status)
                else:
                    status = 1
        except ChildProcessError:
            status = 1

        output = ''.join(output_parts)
        lower = output.lower()

        if status == 0:
            return True, '连接成功'
        if 'permission denied' in lower:
            return False, 'SSH连接失败：root账号或密码错误'
        if 'connection timed out' in lower:
            return False, 'SSH连接失败：连接超时'
        if 'no route to host' in lower:
            return False, 'SSH连接失败：目标不可达'
        if 'connection refused' in lower:
            return False, 'SSH连接失败：端口拒绝连接'
        if 'could not resolve hostname' in lower:
            return False, 'SSH连接失败：IP/主机名无法解析'

        msg = output.strip().split('\n')[-1] if output.strip() else f'ssh返回码={status}'
        return False, f'SSH连接失败：{msg}'
    finally:
        try:
            os.close(fd)
        except Exception:
            pass


async def _verify_root_password(ip: str, password: str, timeout: int = SERVER_AUTH_TIMEOUT) -> tuple[bool, str]:
    """在线程池中验证 root 密码，避免阻塞事件循环。

    参数：
    - ip：目标服务器 IP。
    - password：root 明文密码。
    - timeout：SSH 认证超时时间，单位为秒。

    返回：
    - tuple[bool, str]：认证是否成功和提示信息。
    """
    return await asyncio.to_thread(_verify_root_password_blocking, ip, password, timeout)


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
        raise HTTPException(status_code=500, detail=f'创建系统用户失败：{err or 'unknown error'}')

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
        raise HTTPException(status_code=500, detail=f'删除系统用户失败：{del_err or 'unknown error'}')

    await crud.servers.remove_assigned_user(session, server_id=payload.server_id, username=username)
    return schemas.base.BaseResponse(message='删除用户成功')
