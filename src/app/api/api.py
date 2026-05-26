"""接口聚合模块，负责组合业务路由并暴露统一 API Router。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from fastapi import APIRouter

from app.api.auth import login, user
from app.api.base import ws
from app.api.pspm import envs as pspm_envs
from app.api.pspm import projects as pspm_projects
from app.api.pspm import servers as pspm_servers
from app.api.pspm import terminal as pspm_terminal
from app.api.pspm import users as pspm_users
from app.api.rbac import permissions as rbac_permissions
from app.api.rbac import users

api_router = APIRouter()

api_router.include_router(login.router, tags=['登录'])
api_router.include_router(user.router, prefix='/user', tags=['当前用户'])
api_router.include_router(users.router, prefix='/users', tags=['RBAC 用户'])
api_router.include_router(rbac_permissions.router, prefix='/rbac/permissions', tags=['RBAC 权限'])

api_router.include_router(pspm_users.router, prefix='/pspm/users', tags=['项目管理-用户'])
api_router.include_router(pspm_envs.router, prefix='/pspm/envs', tags=['项目管理-环境'])
api_router.include_router(pspm_servers.router, prefix='/pspm/servers', tags=['项目管理-服务器'])
api_router.include_router(pspm_projects.router, prefix='/pspm/projects', tags=['项目管理-项目'])
api_router.include_router(pspm_terminal.router, prefix='/pspm/terminal', tags=['项目管理-终端'])

api_router.include_router(ws.router, prefix='/ws', tags=['WebSocket'])
