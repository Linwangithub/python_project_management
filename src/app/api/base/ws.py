"""基础 WebSocket 接口模块，提供通用 WebSocket 连接示例与消息处理。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, WebSocketException

from app.api.deps import CurrentWSUser
from app.core.deps import SessionDep

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("", name="ws")
async def index(
    *,
    websocket: WebSocket,
    session: SessionDep,
    current_user: CurrentWSUser,
) -> Any:
    """WebSocket 基础测试接口。

    用于验证基础 WebSocket 连接是否可用。
    """
    await websocket.accept(subprotocol=websocket.headers.get("sec-websocket-protocol"))
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "ping":
                await websocket.send_json({"code": "pong"})
    except WebSocketException as e:
        logger.error(e)
    except WebSocketDisconnect as e:
        logger.warning(e)
