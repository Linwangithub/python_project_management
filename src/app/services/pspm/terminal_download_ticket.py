"""终端一次性下载凭证服务。

本模块维护浏览器原生下载使用的一次性 ticket，负责生成、过期清理和消费校验。
下载 ticket 只保存在后端内存中，使用后立即删除，避免下载链接长期有效。
"""

from __future__ import annotations

import asyncio
import secrets
import time
from typing import Any, Dict

from fastapi import HTTPException

from app.utils.pspm.project_config import TERMINAL_DOWNLOAD_TICKET_TTL_SECONDS
from app.utils.pspm.terminal_config import terminal_message

# 一次性下载凭证内存存储，key 为随机 ticket，value 为远端下载上下文。
terminal_download_tickets: Dict[str, Dict[str, Any]] = {}

# 保护 ticket 存储的异步锁，避免并发创建/消费时出现竞态。
terminal_download_ticket_lock = asyncio.Lock()


async def _cleanup_expired_download_tickets() -> None:
    """清理过期的一次性下载票据。"""
    now = time.time()
    async with terminal_download_ticket_lock:
        expired = [
            ticket
            for ticket, data in terminal_download_tickets.items()
            if float(data.get('expires_at') or 0) <= now
        ]
        for ticket in expired:
            terminal_download_tickets.pop(ticket, None)

async def _create_download_ticket(*, user_id: int, server_row, remote_path: str, filename: str, kind: str) -> str:
    """生成浏览器原生下载使用的一次性 ticket。"""
    await _cleanup_expired_download_tickets()
    ticket = secrets.token_urlsafe(32)
    async with terminal_download_ticket_lock:
        terminal_download_tickets[ticket] = {
            'user_id': int(user_id),
            'server_id': int(getattr(server_row, 'id', 0) or 0),
            'server_ip': str(getattr(server_row, 'ip', '') or ''),
            'ssh_port': int(getattr(server_row, 'ssh_port', 22) or 22),
            'root_password': str(getattr(server_row, 'root_password', '') or ''),
            'remote_path': remote_path,
            'filename': filename,
            'kind': kind,
            'expires_at': time.time() + TERMINAL_DOWNLOAD_TICKET_TTL_SECONDS,
        }
    return ticket

async def _consume_download_ticket(ticket: str) -> Dict[str, Any]:
    """读取并立即删除一次性下载 ticket。"""
    await _cleanup_expired_download_tickets()
    safe_ticket = str(ticket or '').strip()
    if not safe_ticket:
        raise HTTPException(status_code=400, detail=terminal_message('download_ticket_required'))
    async with terminal_download_ticket_lock:
        data = terminal_download_tickets.pop(safe_ticket, None)
    if not data:
        raise HTTPException(status_code=404, detail=terminal_message('download_ticket_missing'))
    if float(data.get('expires_at') or 0) <= time.time():
        raise HTTPException(status_code=404, detail=terminal_message('download_ticket_expired'))
    return data
