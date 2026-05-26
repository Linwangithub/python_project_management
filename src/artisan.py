"""后端运维命令入口。

本模块提供 Typer CLI 命令，用于连通性检查、生成密钥、同步数据库表结构、初始化默认角色权限和演示数据库/Redis 连接。
固定初始化数据统一来自 app.core.bootstrap_config，避免命令脚本中散落硬编码配置。
"""

import asyncio
import logging
import secrets

import typer

from app import crud
from app.core.bootstrap_config import (
    CLI_MESSAGE_ADMIN_CREATED,
    CLI_MESSAGE_ADMIN_EXISTS,
    CLI_MESSAGE_EXAMPLE_END,
    CLI_MESSAGE_EXAMPLE_START,
    CLI_MESSAGE_INIT,
    CLI_MESSAGE_MIGRATE_START,
    CLI_MESSAGE_PONG,
    CLI_MESSAGE_REDIS_SKIPPED,
    CLI_MESSAGE_REFRESH_CREATE,
    CLI_MESSAGE_REFRESH_DROP,
    CLI_MESSAGE_TABLE_READY,
    DEFAULT_ADMIN_USER,
    DEFAULT_PERMISSION_DEFINITIONS,
    DEFAULT_ROLE_DEFINITIONS,
    DEFAULT_SECRET_KEY_BYTES,
    DEFAULT_USER_PERMISSION_KEYS,
    EXAMPLE_DEMO_ID,
    EXAMPLE_REDIS_EXPIRE_SECONDS,
)
from app.core.custom_logging import configure_logging
from app.core.deps import get_settings
from app.core.utils import run_with_connections

app = typer.Typer()

configure_logging(project_log_level=get_settings().dev.log_level)
logger = logging.getLogger(__name__)


@app.command()
def ping() -> None:
    """输出 pong，用于快速确认 CLI 可以正常执行。"""
    typer.echo(CLI_MESSAGE_PONG)


@app.command()
def init() -> None:
    """生成新的 API SECRET_KEY 配置片段。

    输出内容可以复制到后端配置文件中，用于刷新 JWT 签名密钥。
    """
    typer.echo(CLI_MESSAGE_INIT)
    secret_key: str = secrets.token_urlsafe(DEFAULT_SECRET_KEY_BYTES)
    typer.echo(f'API__AUTH0__SECRET_KEY={secret_key}')


@app.command()
def migrate(refresh: bool = typer.Option(False, '--refresh', help='先删除所有表再重新创建，用于强制同步模型变更')) -> None:
    """创建或同步数据库表结构，并初始化默认角色、权限和管理员。

    Args:
        refresh: 是否先删除所有表再重新创建；仅用于测试或明确需要重建模型时。
    """
    typer.echo(CLI_MESSAGE_MIGRATE_START)

    async def create_table(session, refresh: bool = False):
        """根据 ORM 模型同步数据库表结构。

        Args:
            session: run_with_connections 注入的数据库会话，本函数使用 engine 进行建表。
            refresh: 为 True 时先 drop_all 再 create_all。
        """
        from app.core.database import _engine
        from app.models.base import Base

        if refresh:
            typer.echo(CLI_MESSAGE_REFRESH_DROP)
            async with _engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            typer.echo(CLI_MESSAGE_REFRESH_CREATE)

        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        typer.echo(CLI_MESSAGE_TABLE_READY)

    async def create_default_data(session):
        """初始化默认角色、权限、管理员账号和角色绑定关系。

        Args:
            session: run_with_connections 注入的数据库会话。
        """
        from app import models
        from app.crud.base import CRUDBase

        role_crud = CRUDBase(models.rbac.RbacRole)
        permission_crud = CRUDBase(models.rbac.RbacPermission)
        role_permission_crud = CRUDBase(models.rbac.RbacRolePermission)

        roles = {}
        for role_key, role_payload in DEFAULT_ROLE_DEFINITIONS.items():
            role = await crud.rbac.get_role_by_key(session, role_key=role_key)
            if not role:
                role = await role_crud.create(session, obj_in=role_payload)
                typer.echo(f'默认角色已创建：{role_key}')
            roles[role_key] = role

        root_role = roles['root']
        user_role = roles['user']

        permissions = {}
        for permission_key, menu_key, menu_name, action_key, action_name, description in DEFAULT_PERMISSION_DEFINITIONS:
            row = await permission_crud.get(session, {'permission_key': permission_key})
            if not row:
                row = await permission_crud.create(
                    session,
                    obj_in={
                        'permission_key': permission_key,
                        'menu_key': menu_key,
                        'menu_name': menu_name,
                        'action_key': action_key,
                        'action_name': action_name,
                        'description': description,
                        'status': 1,
                    },
                )
            permissions[permission_key] = row

        for perm in permissions.values():
            existed = await role_permission_crud.get(session, {'role_id': root_role.id, 'permission_id': perm.id})
            if not existed:
                await role_permission_crud.create(
                    session,
                    obj_in={'role_id': root_role.id, 'permission_id': perm.id, 'status': 1},
                )

        for key in DEFAULT_USER_PERMISSION_KEYS:
            perm = permissions.get(key)
            if not perm:
                continue
            existed = await role_permission_crud.get(session, {'role_id': user_role.id, 'permission_id': perm.id})
            if not existed:
                await role_permission_crud.create(
                    session,
                    obj_in={'role_id': user_role.id, 'permission_id': perm.id, 'status': 1},
                )

        admin = await crud.users.get(session, obj_in={'username': DEFAULT_ADMIN_USER['username']})
        if admin:
            typer.echo(CLI_MESSAGE_ADMIN_EXISTS)
        else:
            admin = await crud.users.create(session, obj_in=DEFAULT_ADMIN_USER)
            typer.echo(CLI_MESSAGE_ADMIN_CREATED)

        await crud.rbac.bind_user_role(session, user_id=admin.id, role_id=root_role.id)

    async def main(refresh: bool = False):
        """串行执行建表和默认数据初始化。

        Args:
            refresh: 透传给 create_table 的重建表结构开关。
        """
        await run_with_connections(create_table, refresh=refresh, use_redis=False)
        await run_with_connections(create_default_data, use_redis=False)

    asyncio.run(main(refresh))


@app.command()
def example() -> None:
    """执行数据库和 Redis 连接示例，用于排查基础依赖是否可用。"""
    from sqlalchemy import text

    typer.echo(CLI_MESSAGE_EXAMPLE_START)

    async def demo(session, redis_client, demo_id: int):
        """读取数据库时间，并在 Redis 可用时写入演示键。

        Args:
            session: run_with_connections 注入的数据库会话。
            redis_client: run_with_connections 注入的 Redis 客户端，未配置时可能为空。
            demo_id: 演示键 ID，用于构造 Redis key。
        """
        db_result = await session.execute(text('SELECT CURRENT_TIMESTAMP;'))
        current_time = db_result.scalar()
        typer.echo(f'数据库当前时间: {current_time}')

        if redis_client:
            redis_key = f'demo:{demo_id}'
            await redis_client.set(redis_key, str(current_time), ex=EXAMPLE_REDIS_EXPIRE_SECONDS)
            redis_value = await redis_client.get(redis_key)
            typer.echo(f'Redis 存储结果: {redis_key} -> {redis_value}')
        else:
            typer.echo(CLI_MESSAGE_REDIS_SKIPPED)

    asyncio.run(run_with_connections(demo, demo_id=EXAMPLE_DEMO_ID))
    typer.echo(CLI_MESSAGE_EXAMPLE_END)


if __name__ == '__main__':
    app()
