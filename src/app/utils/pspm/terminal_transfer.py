"""终端文件上传、下载和目录浏览工具。

本模块集中维护终端文件传输相关规则，包括下载根目录限制、文件名清洗、
目录打包、远端流式下载和上传落盘。接口层只负责接收请求和组织响应。
"""

from __future__ import annotations

import asyncio
import base64
import posixpath
import re
import shlex
import subprocess

from fastapi import HTTPException

from app.utils.pspm.project_config import TERMINAL_HOME_DIR
from app.utils.pspm.shell_utils import _is_local_server_ip_async, _run_server_shell, _run_shell
from app.utils.pspm.terminal_config import (
    TERMINAL_FILE_KIND_DIR,
    TERMINAL_UPLOAD_EOF,
    terminal_message,
)
from app.utils.pspm.terminal_paths import _normalize_cwd, _resolve_path
from app.utils.pspm.terminal_shell import _build_askpass_ssh_command

# 终端文件传输默认根目录。root 用户限制在 /root；普通用户按 /home/<username> 限制。
HOME_DIR = TERMINAL_HOME_DIR


def _get_transfer_root(current_user) -> str:
    """返回下载目录选择器允许访问的根目录。"""
    username = str(getattr(current_user, 'username', '') or '').strip()
    is_root = int(getattr(current_user, 'id', 0) or 0) == 1 or username == 'root'
    if is_root:
        return HOME_DIR
    safe_username = re.sub(r'[^A-Za-z0-9._-]+', '', username)
    return f'/home/{safe_username or username or "user"}'

def _ensure_under_transfer_root(path: str, root: str) -> str:
    """校验文件传输路径必须位于允许的根目录下。"""
    normalized = _normalize_cwd(path)
    normalized_root = _normalize_cwd(root)
    if normalized == normalized_root or normalized.startswith(f'{normalized_root}/'):
        return normalized
    raise HTTPException(status_code=400, detail=terminal_message('path_outside_root'))

def _resolve_transfer_browser_target(root: str, target_path: str | None) -> str:
    """解析下载目录选择器目标路径，并限制在允许根目录内。"""
    raw = str(target_path or '').strip()
    if not raw:
        return _normalize_cwd(root)
    return _ensure_under_transfer_root(_resolve_path(_normalize_cwd(root), raw), root)

def _safe_transfer_name(name: str, fallback: str = 'pspm_upload') -> str:
    """生成安全的上传/下载文件名。

    参数：
    - name：浏览器上传文件名或远端路径最后一级名称。
    - fallback：清洗后为空时使用的兜底名称。

    返回：
    - 只包含常见安全字符的文件名。
    """
    value = posixpath.basename(str(name or '').strip().replace('\\', '/'))
    value = re.sub(r'[^A-Za-z0-9._\-一-龥]+', '_', value).strip('._')
    return value or fallback

def _resolve_transfer_target(cwd: str, target_path: str | None) -> str:
    """解析上传目标路径。

    参数：
    - cwd：当前终端会话工作目录。
    - target_path：前端传入的目标路径，可为空。

    返回：
    - 标准化后的远端绝对路径。
    """
    raw = str(target_path or '').strip()
    if not raw:
        raw = cwd or HOME_DIR
    return _resolve_path(_normalize_cwd(cwd), raw)

def _safe_transfer_relative_path(relative_path: str | None) -> str:
    """清洗目录上传时的相对路径。

    参数：
    - relative_path：浏览器传入的文件相对目录路径。

    作用：
    - 禁止 `..` 和绝对路径，避免上传越权写入。

    返回：
    - 安全的相对路径；非法时返回空字符串。
    """
    value = str(relative_path or '').strip().replace('\\', '/')
    if not value:
        return ''
    value = posixpath.normpath(value)
    if value in {'.', '..'} or value.startswith('../') or value.startswith('/'):
        return ''
    parts = []
    for raw_part in value.split('/'):
        part = _safe_transfer_name(raw_part, '')
        if not part:
            continue
        parts.append(part)
    return '/'.join(parts)

async def _open_server_download_process(server_row, remote_path: str, kind: str) -> subprocess.Popen:
    """打开一个流式下载进程，让浏览器可以直接显示下载进度。"""
    safe_path = shlex.quote(remote_path)
    if kind == TERMINAL_FILE_KIND_DIR:
        parent = posixpath.dirname(remote_path.rstrip('/')) or '/'
        child = posixpath.basename(remote_path.rstrip('/'))
        remote_command = (
            f'cd {shlex.quote(parent)} && '
            f'if command -v zip >/dev/null 2>&1; then '
            f'zip -r -q - {shlex.quote(child)}; '
            f'else '
            f'python3 -c {shlex.quote("import os,sys,zipfile\\nbase=sys.argv[1]\\nout=zipfile.ZipFile(sys.stdout.buffer, \\'w\\', zipfile.ZIP_DEFLATED)\\nfor root, dirs, files in os.walk(base):\\n    dirs[:] = [d for d in dirs if d not in {\\'.git\\', \\'__pycache__\\'}]\\n    for name in files:\\n        path=os.path.join(root,name)\\n        out.write(path, os.path.relpath(path, os.path.dirname(base)))\\nout.close()")} {shlex.quote(child)}; '
            f'fi'
        )
    else:
        remote_command = f'cat {safe_path}'

    ip = str(getattr(server_row, 'ip', '') or '').strip()
    if await _is_local_server_ip_async(ip):
        return await asyncio.create_subprocess_exec(
            '/bin/bash',
            '-lc',
            remote_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    if not re.match(r'^[A-Za-z0-9_.:-]+$', ip):
        raise HTTPException(status_code=400, detail=f'服务器IP格式不合法：{ip}')

    password = str(getattr(server_row, 'root_password', '') or '')
    ssh_port = int(getattr(server_row, 'ssh_port', 22) or 22)
    ssh_opts = f'-p {ssh_port} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 -o LogLevel=ERROR'
    remote = f'root@{shlex.quote(ip)}'
    quoted_command = shlex.quote(remote_command)
    if password and (await _run_shell('command -v sshpass >/dev/null 2>&1', timeout=5))[0] == 0:
        shell_cmd = f'sshpass -p {shlex.quote(password)} ssh {ssh_opts} {remote} {quoted_command}'
    elif password and (await _run_shell('command -v setsid >/dev/null 2>&1', timeout=5))[0] == 0:
        shell_cmd = _build_askpass_ssh_command(password, f'ssh {ssh_opts} {remote} {quoted_command}')
    elif password:
        raise HTTPException(status_code=500, detail=terminal_message('sshpass_missing_download'))
    else:
        shell_cmd = f'ssh {ssh_opts} -o BatchMode=yes {remote} {quoted_command}'

    return await asyncio.create_subprocess_exec(
        '/bin/bash',
        '-lc',
        shell_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

async def _stream_server_download(server_row, remote_path: str, kind: str):
    """把远端文件或目录流式转发给浏览器。"""
    process = await _open_server_download_process(server_row, remote_path, kind)
    try:
        assert process.stdout is not None
        while True:
            chunk = await process.stdout.read(1024 * 256)
            if not chunk:
                break
            yield chunk
        await process.wait()
    finally:
        if process.returncode is None:
            process.kill()
            await process.communicate()

async def _upload_terminal_file_to_server(server_row, local_path: str, remote_path: str) -> None:
    """把本地临时上传文件写入远端服务器。

    参数：
    - server_row：目标服务器记录。
    - local_path：FastAPI 接收到上传文件后的本地临时文件路径。
    - remote_path：最终写入远端服务器的绝对路径。

    作用：
    - 通过 base64 heredoc 方式跨 SSH 写文件，避免二进制内容被 shell 转义破坏。

    异常：
    - 远端命令执行失败时抛出 HTTP 500。
    """
    parent = posixpath.dirname(remote_path) or HOME_DIR
    with open(local_path, 'rb') as fh:
        encoded = base64.b64encode(fh.read()).decode('ascii')
    script = (
        f'mkdir -p {shlex.quote(parent)} && '
        f'base64 -d > {shlex.quote(remote_path)} <<\'{TERMINAL_UPLOAD_EOF}\'\n'
        f'{encoded}\n'
        f'{TERMINAL_UPLOAD_EOF}\n'
    )
    code, out, err = await _run_server_shell(server_row, script, timeout=1800)
    if code != 0:
        raise HTTPException(status_code=400, detail=(err.strip() or out.strip() or terminal_message('upload_failed')))
