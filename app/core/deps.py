import logging
from functools import cache
from typing import Annotated, AsyncGenerator, Optional
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio.client import Redis as AsyncRedis, RedisError
from app.core.config import Settings
from app.core.helpers import Helpers
from app.core.database import get_session
from app.core.redis import get_redis_client

logger = logging.getLogger(__name__)


@cache
def get_settings() -> Settings:
    """Get settings."""
    return Settings()


@cache
def get_helpers() -> Helpers:
    """Get helpers."""
    return Helpers()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """获取数据库会话"""
    async with get_session() as session:
        try:
            yield session
        except Exception as e:
            await session.rollback()
            raise e
        finally:
            await session.close()


async def get_redis() -> AsyncGenerator[Optional[AsyncRedis], None]:
    if get_settings().redis.uri is None:
        yield None
        return
    async for redis_client in get_redis_client():
        try:
            yield redis_client
        except RedisError as e:
            logger.error(f"Redis操作异常: {str(e)}", exc_info=True)
            raise e
        # finally:
        #     # 可选的连接清理逻辑
        #     await redis_client.close()


SessionDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Optional[AsyncRedis], Depends(get_redis)]
