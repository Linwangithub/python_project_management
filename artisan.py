import asyncio
import logging
import secrets

import typer

from app import crud
from app.core.custom_logging import configure_logging
from app.core.deps import get_settings
from app.core.utils import run_with_connections

app = typer.Typer()

configure_logging(project_log_level=get_settings().dev.log_level)
logger = logging.getLogger(__name__)


@app.command()
def ping() -> None:
    typer.echo('pong')


@app.command()
def init() -> None:
    typer.echo('init')
    secret_key: str = secrets.token_urlsafe(32)
    typer.echo(f'API__AUTH0__SECRET_KEY={secret_key}')


@app.command()
def migrate(refresh: bool = typer.Option(False, '--refresh', help='先删除所有表再重新创建，用于强制同步模型变更')) -> None:
    typer.echo('开始使用 ORM 模型创建/同步表...')

    async def create_table(session, refresh: bool = False):
        from app.core.database import _engine
        from app.models.base import Base

        if refresh:
            typer.echo('检测到 --refresh，正在删除所有表...')
            async with _engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            typer.echo('表删除完成，开始重新创建...')

        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        typer.echo('表结构同步完成。')

    async def create_default_data(session):
        from app import models
        from app.crud.base import CRUDBase

        role_crud = CRUDBase(models.rbac.RbacRole)
        permission_crud = CRUDBase(models.rbac.RbacPermission)
        role_permission_crud = CRUDBase(models.rbac.RbacRolePermission)

        root_role = await crud.rbac.get_role_by_key(session, role_key='root')
        if not root_role:
            root_role = await role_crud.create(
                session,
                obj_in={
                    'role_key': 'root',
                    'role_name': 'root用户',
                    'description': '超级管理员',
                    'status': 1,
                },
            )
            typer.echo('默认角色已创建：root')

        user_role = await crud.rbac.get_role_by_key(session, role_key='user')
        if not user_role:
            user_role = await role_crud.create(
                session,
                obj_in={
                    'role_key': 'user',
                    'role_name': '普通用户',
                    'description': '普通成员',
                    'status': 1,
                },
            )
            typer.echo('默认角色已创建：user')

        permission_defs = [
            ('user_management:view', 'user_management', '用户管理', None, None, '菜单可见'),
            ('user_management:create', 'user_management', '用户管理', 'create', '新增', '新增用户'),
            ('user_management:delete', 'user_management', '用户管理', 'delete', '删除', '删除用户'),
            ('user_management:update', 'user_management', '用户管理', 'update', '更新', '更新用户'),
            ('user_management:update_password', 'user_management', '用户管理', 'update_password', '更新密码', '更新用户密码'),

            ('project_management:view', 'project_management', '项目管理', None, None, '菜单可见'),
            ('project_management:create', 'project_management', '项目管理', 'create', '创建', '创建项目'),
            ('project_management:setting', 'project_management', '项目管理', 'setting', '设置', '设置项目端口/命令'),
            ('project_management:start_foreground', 'project_management', '项目管理', 'start_foreground', '前台启动', '前台启动项目'),
            ('project_management:start_background', 'project_management', '项目管理', 'start_background', '后台启动', '后台启动项目'),
            ('project_management:deploy_start', 'project_management', '项目管理', 'deploy_start', '部署启动', '部署方式启动项目'),
            ('project_management:stop', 'project_management', '项目管理', 'stop', '停止服务', '停止项目服务'),
            ('project_management:copy', 'project_management', '项目管理', 'copy', '复制', '复制项目'),
            ('project_management:export', 'project_management', '项目管理', 'export', '导出', '导出项目'),
            ('project_management:delete', 'project_management', '项目管理', 'delete', '删除', '删除项目'),

            ('env_management:view', 'env_management', '环境管理', None, None, '菜单可见'),
            ('env_management:create', 'env_management', '环境管理', 'create', '创建', '创建环境'),
            ('env_management:delete', 'env_management', '环境管理', 'delete', '删除', '删除环境'),

            ('server_management:view', 'server_management', '服务器管理', None, None, '菜单可见'),
            ('server_management:create', 'server_management', '服务器管理', 'create', '创建', '创建服务器'),
            ('server_management:assign', 'server_management', '服务器管理', 'assign', '分配用户', '服务器分配用户'),
            ('server_management:delete', 'server_management', '服务器管理', 'delete', '删除', '删除服务器'),
        ]

        permissions = {}
        for permission_key, menu_key, menu_name, action_key, action_name, description in permission_defs:
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

        user_allow_keys = [
            'project_management:view',
            'project_management:create',
            'project_management:setting',
            'project_management:start_foreground',
            'project_management:start_background',
            'project_management:deploy_start',
            'project_management:stop',
            'project_management:copy',
            'project_management:export',
            'project_management:delete',
            'env_management:view',
            'env_management:create',
            'env_management:delete',
        ]
        for key in user_allow_keys:
            perm = permissions.get(key)
            if not perm:
                continue
            existed = await role_permission_crud.get(session, {'role_id': user_role.id, 'permission_id': perm.id})
            if not existed:
                await role_permission_crud.create(
                    session,
                    obj_in={'role_id': user_role.id, 'permission_id': perm.id, 'status': 1},
                )

        admin = await crud.users.get(session, obj_in={'username': 'admin'})
        if admin:
            typer.echo('默认用户已存在')
        else:
            admin = await crud.users.create(
                session,
                obj_in={
                    'userid': 1,
                    'username': 'admin',
                    'password': '123456',
                },
            )
            typer.echo('默认用户已创建：admin / 123456')

        await crud.rbac.bind_user_role(session, user_id=admin.id, role_id=root_role.id)

        # server = await crud.servers.get(session, obj_in={'ip': '127.0.0.1', 'ssh_port': 22, 'status': 1})
        # if not server:
        #     server = await crud.servers.create(
        #         session,
        #         obj_in={
        #             'alias': 'centos7',
        #             'ip': '192.168.93.129',
        #             'ssh_port': 22,
        #             'middlewares': '',
        #             'status': 1,
        #             'created_by': admin.id,
        #         },
        #     )
        #     typer.echo('默认服务器已创建：192.168.93.129')

        # env = await crud.envs.get(session, obj_in={'owner_id': admin.id, 'env_name': 'demo_api', 'status': 1})
        # if not env:
        #     env = await crud.envs.create(
        #         session,
        #         obj_in={
        #             'owner_id': admin.id,
        #             'env_name': 'demo_api',
        #             'project_name': 'demo_api',
        #             'python_version': '3.11',
        #             'main_packages': 'fastapi=0.115.*, sqlalchemy=2.*',
        #             'status': 1,
        #             'created_by': admin.id,
        #         },
        #     )
        #     typer.echo('默认环境已创建：demo_api')

        # project = await crud.projects.get(session, obj_in={'owner_id': admin.id, 'name': 'demo_api', 'status': [0, 1]})
        # if not project:
        #     await crud.projects.create(
        #         session,
        #         obj_in={
        #             'owner_id': admin.id,
        #             'server_id': server.id if server else None,
        #             'env_id': env.id if env else None,
        #             'name': 'demo_api',
        #             'description': '示例项目',
        #             'backend_path': '/root/project/demo_api/backend',
        #             'frontend_path': '/root/project/demo_api/frontend',
        #             'nginx_conf_path': '/etc/nginx/conf.d/demo_api.conf',
        #             'frontend_port': '15173',
        #             'backend_dev_port': '18080',
        #             'backend_deploy_port': '18081',
        #             'database_name': 'demo_api',
        #             'conda_env_name': 'demo_api',
        #             'python_version': '3.11',
        #             'dev_start_command': 'conda run -n demo_api python main.py --port 18080',
        #             'deploy_start_command': 'conda run -n demo_api uvicorn main:app --host 0.0.0.0 --port 18081',
        #             'status': 0,
        #             'created_by': admin.id,
        #         },
        #     )
            # typer.echo('默认项目已创建：demo_api')

    async def main(refresh: bool = False):
        await run_with_connections(create_table, refresh=refresh, use_redis=False)
        await run_with_connections(create_default_data, use_redis=False)

    asyncio.run(main(refresh))


@app.command()
def example() -> None:
    from sqlalchemy import text

    typer.echo('=== 开始数据库和 Redis 连接示例 ===')

    async def demo(session, redis_client, demo_id: int):
        db_result = await session.execute(text('SELECT CURRENT_TIMESTAMP;'))
        current_time = db_result.scalar()
        typer.echo(f'数据库当前时间: {current_time}')

        if redis_client:
            redis_key = f'demo:{demo_id}'
            await redis_client.set(redis_key, str(current_time), ex=3600)
            redis_value = await redis_client.get(redis_key)
            typer.echo(f'Redis 存储结果: {redis_key} -> {redis_value}')
        else:
            typer.echo('Redis 未配置，跳过 Redis 示例。')

    asyncio.run(run_with_connections(demo, demo_id=1001))
    typer.echo('=== 示例结束 ===')


if __name__ == '__main__':
    app()
