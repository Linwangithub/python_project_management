"""终端文件传输服务模块。

用途：
- 承接终端上传、下载凭证、旧版下载和目录浏览的业务逻辑。
- API 层只负责接收 HTTP 参数并返回 FastAPI Response。
"""

from __future__ import annotations

import base64
import os
import posixpath
import shlex
import tempfile
from types import SimpleNamespace
from urllib.parse import quote

from fastapi import HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse

from app import schemas
from app.services.pspm.terminal_context import _get_transfer_session_context
from app.services.pspm.terminal_download_ticket import (
    _consume_download_ticket,
    _create_download_ticket,
    terminal_download_ticket_lock,
    terminal_download_tickets,
)
from app.utils.pspm.project_config import TERMINAL_DOWNLOAD_TICKET_TTL_SECONDS
from app.utils.pspm.shell_utils import _run_server_shell
from app.utils.pspm.terminal_config import (
    TERMINAL_FILE_KIND_DIR,
    TERMINAL_FILE_KIND_FILE,
    TERMINAL_FILE_KIND_MISSING,
    TERMINAL_MEDIA_TYPE_BINARY,
    TERMINAL_MEDIA_TYPE_ZIP,
    terminal_message,
)
from app.utils.pspm.terminal_paths import _normalize_cwd
from app.utils.pspm.terminal_transfer import (
    _ensure_under_transfer_root,
    _get_transfer_root,
    _resolve_transfer_browser_target,
    _resolve_transfer_target,
    _safe_transfer_name,
    _safe_transfer_relative_path,
    _stream_server_download,
    _upload_terminal_file_to_server,
)


def _terminal_content_disposition(filename: str) -> dict[str, str]:
    """构造浏览器下载响应头。

    参数：
    - filename：下载文件名。

    返回：
    - dict：包含 Content-Disposition 的响应头。
    """
    header_name = quote(filename)
    return {'Content-Disposition': f"attachment; filename*=UTF-8''{header_name}"}


def _terminal_download_filename(base_name: str, kind: str) -> str:
    """根据远端资源类型生成下载文件名。

    参数：
    - base_name：远端路径最后一级名称。
    - kind：file 或 dir。

    返回：
    - str：目录自动追加 .zip，文件保持原名。
    """
    return f'{base_name}.zip' if kind == TERMINAL_FILE_KIND_DIR else base_name


def _terminal_download_media_type(kind: str) -> str:
    """根据远端资源类型返回下载响应 media type。"""
    return TERMINAL_MEDIA_TYPE_ZIP if kind == TERMINAL_FILE_KIND_DIR else TERMINAL_MEDIA_TYPE_BINARY


async def _detect_terminal_remote_kind_and_size(server_row, remote_path: str) -> tuple[str, int]:
    """检测远端下载目标是文件、目录还是不存在，并返回文件大小。

    参数：
    - server_row：终端会话绑定的服务器。
    - remote_path：远端目标路径。

    返回：
    - tuple[str, int]：资源类型和字节大小；目录大小返回 0。
    """
    quoted_path = shlex.quote(remote_path)
    check_script = (
        f'if [ -d {quoted_path} ]; then echo "{TERMINAL_FILE_KIND_DIR}\t0"; '
        f'elif [ -f {quoted_path} ]; then printf "{TERMINAL_FILE_KIND_FILE}\t%s\n" "$(stat -c %s {quoted_path} 2>/dev/null || echo 0)"; '
        f'else echo "{TERMINAL_FILE_KIND_MISSING}\t0"; fi'
    )
    code, out, _err = await _run_server_shell(server_row, check_script, timeout=10)
    last_line = (out or '').strip().splitlines()[-1] if code == 0 and (out or '').strip() else f'{TERMINAL_FILE_KIND_MISSING}\t0'
    kind, _, size_text = last_line.partition('\t')
    size = int(size_text or 0) if str(size_text or '').isdigit() else 0
    return kind.strip(), size


async def upload_terminal_file_service(
    *,
    session,
    current_user,
    session_id: str,
    target_path: str,
    relative_path: str,
    file: UploadFile,
    ws_terminal_lock,
    ws_terminal_sessions,
) -> str:
    """上传文件到当前终端会话所在服务器。

    返回：
    - str：最终写入的远端路径。
    """
    server_row, cwd = await _get_transfer_session_context(session, current_user, session_id)
    target = _resolve_transfer_target(cwd, target_path)
    safe_name = _safe_transfer_name(file.filename or 'upload.bin', 'upload.bin')
    safe_relative_path = _safe_transfer_relative_path(relative_path)
    if safe_relative_path:
        remote_path = posixpath.join(target, safe_relative_path)
    else:
        code, _out, _err = await _run_server_shell(server_row, f'test -d {shlex.quote(target)}', timeout=10)
        remote_path = posixpath.join(target, safe_name) if code == 0 else target

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            tmp.write(chunk)
        tmp.flush()
    try:
        await _upload_terminal_file_to_server(server_row, tmp_path, remote_path)
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
    return remote_path


async def create_terminal_download_ticket_service(
    *,
    session,
    current_user,
    session_id: str,
    path: str,
    ws_terminal_lock,
    ws_terminal_sessions,
) -> dict[str, object]:
    """创建浏览器原生下载使用的一次性凭证。"""
    server_row, _cwd = await _get_transfer_session_context(session, current_user, session_id)
    root = _get_transfer_root(current_user)
    remote_path = _resolve_transfer_browser_target(root, path)
    base_name = _safe_transfer_name(posixpath.basename(remote_path.rstrip('/')) or 'download', 'download')
    kind, size = await _detect_terminal_remote_kind_and_size(server_row, remote_path)
    if kind == TERMINAL_FILE_KIND_MISSING:
        raise HTTPException(status_code=404, detail=terminal_message('file_missing'))
    filename = _terminal_download_filename(base_name, kind)
    ticket = await _create_download_ticket(
        user_id=current_user.id,
        server_row=server_row,
        remote_path=remote_path,
        filename=filename,
        kind=kind,
    )
    async with terminal_download_ticket_lock:
        if ticket in terminal_download_tickets:
            terminal_download_tickets[ticket]['size'] = size
    return {'ticket': ticket, 'filename': filename, 'size': size, 'expires_in': TERMINAL_DOWNLOAD_TICKET_TTL_SECONDS}


async def download_terminal_file_direct_service(ticket: str) -> StreamingResponse:
    """使用一次性下载凭证创建流式下载响应。"""
    data = await _consume_download_ticket(ticket)
    server_row = SimpleNamespace(
        id=data.get('server_id'),
        ip=data.get('server_ip'),
        ssh_port=data.get('ssh_port') or 22,
        root_password=data.get('root_password') or '',
    )
    remote_path = str(data.get('remote_path') or '')
    kind = str(data.get('kind') or TERMINAL_FILE_KIND_FILE)
    filename = str(data.get('filename') or 'download')
    headers = _terminal_content_disposition(filename)
    size = int(data.get('size') or 0)
    if kind == TERMINAL_FILE_KIND_FILE and size > 0:
        headers['Content-Length'] = str(size)
    return StreamingResponse(
        _stream_server_download(server_row, remote_path, kind),
        media_type=_terminal_download_media_type(kind),
        headers=headers,
    )


async def download_terminal_file_legacy_service(
    *,
    session,
    current_user,
    session_id: str,
    path: str,
    ws_terminal_lock,
    ws_terminal_sessions,
) -> Response:
    """旧版下载接口：先在后端取完整内容，再一次性返回。

    说明：
    - 新前端优先使用 download-ticket + download-direct。
    - 保留本接口是为了兼容旧调用方。
    """
    server_row, _cwd = await _get_transfer_session_context(session, current_user, session_id)
    root = _get_transfer_root(current_user)
    remote_path = _resolve_transfer_browser_target(root, path)
    base_name = _safe_transfer_name(posixpath.basename(remote_path.rstrip('/')) or 'download', 'download')
    kind, _size = await _detect_terminal_remote_kind_and_size(server_row, remote_path)
    if kind == TERMINAL_FILE_KIND_MISSING:
        raise HTTPException(status_code=404, detail=terminal_message('file_missing'))

    quoted_path = shlex.quote(remote_path)
    if kind == TERMINAL_FILE_KIND_DIR:
        filename = f'{base_name}.zip'
        parent = posixpath.dirname(remote_path.rstrip('/')) or '/'
        child = posixpath.basename(remote_path.rstrip('/'))
        zip_script = (
            f'cd {shlex.quote(parent)} && '
            f'if command -v zip >/dev/null 2>&1; then '
            f'zip -r -q - {shlex.quote(child)}; '
            f'else '
            f'python3 -c {shlex.quote("import os,sys,zipfile\nbase=sys.argv[1]\nout=zipfile.ZipFile(sys.stdout.buffer, \'w\', zipfile.ZIP_DEFLATED)\nfor root, dirs, files in os.walk(base):\n    dirs[:] = [d for d in dirs if d not in {\'.git\', \'__pycache__\'}]\n    for name in files:\n        path=os.path.join(root,name)\n        out.write(path, os.path.relpath(path, os.path.dirname(base)))\nout.close()")} {shlex.quote(child)}; '
            f'fi'
        )
        cmd = f'{zip_script} | base64 -w 0'
    else:
        filename = base_name
        cmd = f'base64 -w 0 {quoted_path}'

    code, out, err = await _run_server_shell(server_row, cmd, timeout=1800)
    if code != 0:
        raise HTTPException(status_code=400, detail=(err.strip() or out.strip() or terminal_message('download_failed')))
    try:
        content = base64.b64decode((out or '').strip())
    except Exception:
        raise HTTPException(status_code=500, detail=terminal_message('download_parse_failed'))
    return Response(content=content, media_type=_terminal_download_media_type(kind), headers=_terminal_content_disposition(filename))


async def list_terminal_path_service(
    *,
    session,
    current_user,
    session_id: str,
    path: str,
    ws_terminal_lock,
    ws_terminal_sessions,
) -> dict[str, object]:
    """列出当前终端会话允许下载根目录内的文件和目录。"""
    server_row, _cwd = await _get_transfer_session_context(session, current_user, session_id)
    root = _get_transfer_root(current_user)
    target = _resolve_transfer_browser_target(root, path)
    quoted_target = shlex.quote(target)
    script = (
        f'target={quoted_target}; '
        'if [ -d "$target" ]; then '
        'find "$target" -maxdepth 1 -mindepth 1 -printf "%y\t%p\n" 2>/dev/null | sort; '
        'elif [ -f "$target" ]; then '
        'printf "f\t%s\n" "$target"; '
        'else exit 44; fi'
    )
    code, out, err = await _run_server_shell(server_row, script, timeout=20)
    if code != 0:
        raise HTTPException(status_code=404, detail=(err.strip() or out.strip() or terminal_message('file_missing')))

    items = []
    for line in (out or '').splitlines():
        if '\t' not in line:
            continue
        kind, item_path = line.split('\t', 1)
        name = posixpath.basename(item_path.rstrip('/')) or item_path
        if name.startswith('.'):
            continue
        items.append({'name': name, 'path': item_path, 'type': TERMINAL_FILE_KIND_DIR if kind == 'd' else TERMINAL_FILE_KIND_FILE})

    normalized_root = _normalize_cwd(root)
    if target == normalized_root:
        parent = normalized_root
        can_go_parent = False
    else:
        parent = posixpath.dirname(target.rstrip('/')) or normalized_root
        if parent == '.':
            parent = normalized_root
        parent = _ensure_under_transfer_root(parent, normalized_root)
        can_go_parent = True

    return {'cwd': target, 'root': normalized_root, 'parent': parent, 'can_go_parent': can_go_parent, 'items': items}
