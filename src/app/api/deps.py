"""接口依赖模块，提供接口层复用的鉴权、分页和数据库会话依赖。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

import logging
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, WebSocket, WebSocketException, status
from fastapi.security import OAuth2PasswordBearer
from jwt.exceptions import InvalidTokenError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, schemas
from app.core.deps import get_db, get_settings

logger = logging.getLogger(__name__)

reusable_oauth = OAuth2PasswordBearer(tokenUrl=f'{get_settings().dev.api_str}/oauth-login')
TokenDep = Annotated[str, Depends(reusable_oauth)]


async def get_token_payload(token: str) -> schemas.token.TokenPayload:
    """解析并校验 JWT 令牌载荷。

    返回 TokenPayload；令牌无效时抛出 401。
    """
    try:
        payload = jwt.decode(
            token,
            get_settings().api.auth0.secret_key,
            algorithms=[get_settings().api.auth0.algorithm],
        )
        token_data = schemas.token.TokenPayload(**payload)
    except (InvalidTokenError, ValidationError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Unauthorized')
    return token_data


async def get_current_user(token: TokenDep, db: AsyncSession = Depends(get_db)) -> schemas.users.Data:
    """根据访问令牌查询当前用户。

    从数据库读取用户记录并转换为用户响应模型。
    """
    token_data = await get_token_payload(token)
    user = await crud.users.get(db, {'id': token_data.sub})
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Unauthorized')
    return schemas.users.Data.model_validate(user)


async def get_current_active_user(current_user: schemas.users.Data = Depends(get_current_user)) -> schemas.users.Data:
    """校验当前用户是否处于启用状态。

    禁用用户会被拒绝访问。
    """
    if not await crud.users.is_active(current_user):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Inactive User')
    return current_user


def _extract_ws_token(websocket: WebSocket) -> str:
    """从 WebSocket 请求中提取登录令牌。

    前端终端窗口通过 WebSocket 建立独立会话。浏览器不能直接给
    WebSocket 设置 Authorization 请求头，所以这里优先从 query string
    的 token 参数取值；同时兼容旧版本从 Sec-WebSocket-Protocol 传 JWT
    的方式，避免已经打开的旧前端页面立即失效。

    参数：
    - websocket：FastAPI 注入的 WebSocket 连接对象。

    返回：
    - str：JWT 登录令牌；如果没有携带则返回空字符串。
    """
    query_token = str(websocket.query_params.get('token') or '').strip()
    if query_token:
        return query_token

    raw_protocol = str(websocket.headers.get('sec-websocket-protocol') or '').strip()
    for item in raw_protocol.split(','):
        candidate = item.strip()
        if candidate and candidate != 'pspm-terminal':
            return candidate
    return ''


async def get_ws_current_user(websocket: WebSocket, db: AsyncSession = Depends(get_db)) -> schemas.users.Data:
    """根据 WebSocket 请求令牌查询当前用户。

    用于终端 WebSocket 连接鉴权。
    """
    try:
        token = _extract_ws_token(websocket)
        payload = jwt.decode(
            token,
            get_settings().api.auth0.secret_key,
            algorithms=[get_settings().api.auth0.algorithm],
        )
        token_data = schemas.token.TokenPayload(**payload)
    except (InvalidTokenError, ValidationError):
        raise WebSocketException(status.HTTP_401_UNAUTHORIZED, 'JWT 无法验证凭据')

    user = await crud.users.get(db, {'id': token_data.sub})
    if not user:
        raise WebSocketException(status.HTTP_401_UNAUTHORIZED, 'Unauthorized')
    return schemas.users.Data.model_validate(user)


async def get_ws_current_active_user(current_user: schemas.users.Data = Depends(get_ws_current_user)) -> schemas.users.Data:
    """校验 WebSocket 当前用户是否处于启用状态。"""
    if not await crud.users.is_active(current_user):
        raise WebSocketException(status.HTTP_401_UNAUTHORIZED, 'Unauthorized')
    return current_user


CurrentUser = Annotated[schemas.users.Data, Depends(get_current_active_user)]
CurrentWSUser = Annotated[schemas.users.Data, Depends(get_ws_current_active_user)]


def require_permission(menu_key: str, action_key: str | None = None):
    """构造菜单权限检查依赖。

    参数为菜单 key 和动作 key，返回可被 FastAPI Depends 使用的权限校验器。
    """
    async def _checker(
        current_user: schemas.users.Data = Depends(get_current_active_user),
        db: AsyncSession = Depends(get_db),
    ) -> schemas.users.Data:
        """执行 FastAPI 权限依赖检查。

        参数：
        - current_user：当前登录用户，由登录态依赖解析得到。
        - db：数据库会话，用于查询 RBAC 权限表。

        返回：
        - 当前用户数据；无权限时抛出 HTTP 403。
        """
        has = await crud.rbac.has_permission(db, user_id=current_user.id, menu_key=menu_key, action_key=action_key)
        if not has:
            raise HTTPException(status_code=403, detail='无权限')
        return current_user

    return _checker
