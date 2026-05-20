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
