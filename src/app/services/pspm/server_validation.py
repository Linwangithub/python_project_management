"""服务器管理输入校验和连接验证服务。

本模块集中维护服务器 IP、用户名、root 密码校验，以及 ping/SSH 密码验证逻辑。
接口层只负责权限依赖、请求响应和调用 CRUD。
"""

from __future__ import annotations

import asyncio
import os
import pty
import select
import shlex
import time

from fastapi import HTTPException

from app import crud

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
