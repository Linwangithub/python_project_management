import logging
from typing import Callable, Awaitable
from app.core.deps import get_settings
from app.core.database import connect_to_database, close_database_connection, get_session
from app.core.redis import connect_to_redis, close_redis_connection, redis_client
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def run_with_connections(
    func: Callable[..., Awaitable[None]],
    *args,
    use_db: bool = True,
    use_redis: bool = True,
    **kwargs
) -> None:
    """
    通用连接管理工具：自动处理数据库和Redis的连接生命周期

    Args:
        func: 业务逻辑函数，参数格式：(session, redis_client, *args, **kwargs)
        *args: 传递给业务函数的位置参数
        use_db: 是否启用数据库连接（默认True）
        use_redis: 是否启用Redis连接（默认True）
        **kwargs: 传递给业务函数的关键字参数
    """
    settings = get_settings()
    session: AsyncSession | None = None
    try:
        # 建立连接
        if use_db:
            await connect_to_database(settings.db.uri)
        if use_redis and settings.redis.uri:
            await connect_to_redis(settings.redis.uri)
            from app.core.redis import redis_client
            # 新增：验证Redis连接是否成功
            if redis_client is None:
                raise RuntimeError(f"Redis连接失败，客户端未初始化 {settings.redis.uri}")
            # 新增：测试Redis连接可用性
            await redis_client.ping()
        logger.info(f"✅ 连接成功（DB: {use_db}, Redis: {use_redis}）")

        # 准备业务函数参数（不含数据库会话）
        func_args = []
        if use_redis:
            from app.core.redis import redis_client
            func_args.append(redis_client)  # 传递Redis客户端
        func_args.extend(args)  # 添加用户自定义参数

        # 执行业务逻辑（修复数据库会话获取方式）
        if use_db:
            # 使用异步上下文管理器正确获取会话
            async with get_session() as session:
                # 将session作为第一个参数传入业务函数
                await func(session, *func_args, **kwargs)
        else:
            await func(*func_args, **kwargs)

    except Exception as e:
        logger.error(f"❌ 操作失败: {str(e)}", exc_info=True)
        raise
    finally:
        # 只关闭当前会话，不关闭全局连接池
        if session and use_db:
            await session.close()
        # if use_redis:
        #     await close_redis_connection()
        # if use_db:
        #     await close_database_connection()
        logger.info(f"🔌 连接已关闭（DB: {use_db}, Redis: {use_redis}）")
