"""数据库连接和会话管理模块。"""
import logging
from typing import Optional, AsyncGenerator
from pydantic_core import MultiHostUrl
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine, AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

_engine: Optional[AsyncEngine] = None
_SessionFactory: Optional[async_sessionmaker[AsyncSession]] = None


async def connect_to_database(uri: str | MultiHostUrl) -> None:
    """在应用启动时，初始化全局的数据库引擎和会话工厂。"""
    global _engine, _SessionFactory
    if _engine is not None:
        logger.info("数据库已初始化，跳过重复设置。")
        return
    _engine = create_async_engine(
        str(uri),
        pool_size=10,  # 连接池初始大小
        max_overflow=20,  # 连接池最大溢出数量
        pool_recycle=3600,  # 连接回收时间
    )
    _SessionFactory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    logger.info("✅ 数据库引擎和会话工厂已成功创建")


async def close_database_connection() -> None:
    """在应用关闭时，关闭全局的数据库引擎连接池。"""
    global _engine, _SessionFactory
    if _engine:
        await _engine.dispose()
        _engine = None
        _SessionFactory = None
        logger.info("🔌 数据库引擎连接池已关闭")


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """在应用启动时，初始化全局的数据库引擎和会话工厂。"""
    global _engine, _SessionFactory
    if _SessionFactory is None:
        raise RuntimeError("数据库会话工厂未初始化，请先调用connect_to_database")
    async with _SessionFactory() as session:
        yield session
