"""终端 WebSocket 响应和 PTY 基础工具。

本模块只放不依赖业务数据库的 WebSocket 小工具，包括响应结构、ANSI 清理、
shell 标记值提取、安全发送和 PTY 写入。复杂会话状态由服务层维护。
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict

from fastapi import WebSocket

# 终端 ANSI 控制序列匹配规则，用于清理带颜色或光标控制的 shell 输出。
ANSI_PATTERN = re.compile(r'\[[0-?]*[ -/]*[@-~]')


def _terminal_ws_response(message: str, data: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """构造终端 WebSocket 返回给前端的 JSON 消息。"""
    return {'type': message, 'data': data or {}}

def _strip_ansi(text: str) -> str:
    """移除终端输出中的 ANSI 控制序列。"""
    return ANSI_PATTERN.sub('', str(text or ''))

def _extract_ws_marked_value(output: str, key: str) -> str:
    """从 shell 输出中提取指定 KEY 对应的值。"""
    prefix = f'{key}='
    for line in str(output or '').splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ''

async def _safe_send_json(websocket: WebSocket, payload: Dict[str, Any]) -> bool:
    """安全发送 WebSocket JSON 消息，连接已关闭时返回 False。"""
    try:
        await websocket.send_json(payload)
        return True
    except Exception:
        return False

def _write_pty(master_fd: int, text: str) -> None:
    """把浏览器输入写入 PTY 主端。"""
    os.write(master_fd, str(text or '').encode('utf-8', errors='replace'))
