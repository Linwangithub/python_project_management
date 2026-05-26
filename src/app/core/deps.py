"""核心依赖模块，封装数据库会话、当前用户和配置读取等公共依赖。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

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
    """获取应用配置对象。"""
    return Settings()


@cache
def get_helpers() -> Helpers:
    """获取通用辅助工具对象。"""
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
    """获取 Redis 连接依赖。

    供需要 Redis 的接口通过依赖注入使用。
    """
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
