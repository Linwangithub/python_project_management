"""System scheduler tasks.

这里保留一个与业务无关的通用定时任务示例，方便项目模板直接复用。
"""

from datetime import datetime
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def heartbeat_task() -> None:
    """通用心跳任务：用于验证调度器可正常执行。"""
    logger.info("✅ scheduler heartbeat: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


def setup_scheduler() -> None:
    """配置并启动系统定时任务。"""
    if scheduler.running:
        logger.info("定时任务调度器已启动，跳过重复启动")
        return

    scheduler.add_job(
        heartbeat_task,
        trigger="date",
        run_date=datetime.now(),
        id="startup_heartbeat",
        replace_existing=True,
    )
    scheduler.add_job(
        heartbeat_task,
        trigger=IntervalTrigger(minutes=60),
        id="heartbeat_job",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("✅ 定时任务调度器已启动")


def shutdown_scheduler() -> None:
    """关闭调度器。"""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("✅ 定时任务调度器已关闭")
