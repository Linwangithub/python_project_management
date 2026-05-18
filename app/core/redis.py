import logging
from typing import AsyncGenerator
from pydantic_core import MultiHostUrl
from contextlib import asynccontextmanager
import redis.asyncio as redis
from redis.asyncio.client import Redis as AsyncRedis

logger = logging.getLogger(__name__)

redis_client: AsyncRedis | None = None


async def connect_to_redis(uri: str | MultiHostUrl | None) -> None:
    """建立Redis连接"""
    global redis_client
    if redis_client is not None:
        logger.info("Redis已初始化，跳过重复设置。")
        return
    if uri is None:
        logger.error("Redis URI未配置")
        return
    try:
        # redis_client = await redis.from_url(str(uri), decode_responses=True)
        pool = redis.ConnectionPool.from_url(str(uri), decode_responses=True)
        redis_client = redis.Redis(connection_pool=pool)
        # 测试连接
        await redis_client.ping()
        logger.info(f"✅ 成功连接到Redis")
    except Exception as e:
        logger.error(f"❌ Redis连接失败: {str(e)}", exc_info=True)
        raise


async def close_redis_connection() -> None:
    """应用停止时关闭连接池（不是在请求中关闭）"""
    global redis_client
    if redis_client:
        await redis_client.connection_pool.disconnect()
        # await redis_client.close()
        redis_client = None
        logger.info("🔌 Redis连接已关闭")


async def get_redis_client() -> AsyncGenerator[AsyncRedis, None]:
    """获取Redis客户端（异步生成器）"""
    if redis_client is None:
        raise RuntimeError("Redis连接尚未初始化，请先调用connect_to_redis")

    try:
        yield redis_client
    except Exception as e:
        logger.error(f"Redis客户端使用出错: {str(e)}", exc_info=True)
        raise
