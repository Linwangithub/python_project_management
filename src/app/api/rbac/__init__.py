"""权限接口包入口模块，提供 RBAC 管理相关路由分组。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from app.api.rbac import permissions, users

__all__ = ['users', 'permissions']
