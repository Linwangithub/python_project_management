"""路由注册模块，负责把各业务接口挂载到统一 API 前缀下。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from fastapi import FastAPI
from app.core.deps import get_settings
from app.api.api import api_router


def register(app: FastAPI) -> None:
    """注册模块组件或处理用户注册。

    在路由模块中用于创建用户并绑定默认角色；在核心模块中用于注册应用能力。
    """
    app.include_router(api_router, prefix=get_settings().dev.api_str)
