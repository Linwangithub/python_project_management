import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.custom_logging import configure_logging
from app.core.database import close_database_connection, connect_to_database
from app.core.deps import get_settings
from app.core.exceptions import register as register_exceptions
from app.core.middleware import register as register_middleware
from app.core.redis import close_redis_connection, connect_to_redis
from app.core.sys_apscheduler import setup_scheduler, shutdown_scheduler
from app.routers.api import register as register_router

logger = logging.getLogger("app.create_app")


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore
    settings = get_settings()

    configure_logging(project_log_level=settings.dev.log_level)

    await connect_to_database(settings.db.uri)
    await connect_to_redis(settings.redis.uri)
    setup_scheduler()

    try:
        logger.info("🚀 应用启动，数据库和 Redis 已连接。")
        yield
    finally:
        shutdown_scheduler()
        await close_redis_connection()
        await close_database_connection()
        logger.info("应用关闭，连接资源已释放。")


def create_app() -> FastAPI:
    app = FastAPI(
        title=get_settings().dev.project_name,
        version="0.0.1",
        openapi_url=get_settings().dev.openapi_url,
        lifespan=lifespan,
    )
    register_middleware(app)
    register_exceptions(app)
    register_router(app)
    return app
