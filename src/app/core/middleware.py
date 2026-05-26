"""中间件模块，负责请求日志、跨域和异常链路等应用级处理。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from asgi_correlation_id import CorrelationIdMiddleware

from app.core.deps import get_settings


def register(app: FastAPI) -> None:
    """注册应用全局中间件。

    作用：集中安装请求链路 ID、CORS 和可信 Host 中间件。
    """
    correlation_id_middleware(app)
    cors_middleware(app)
    trusted_host_middleware(app)


def cors_middleware(app: FastAPI) -> None:
    """注册 CORS 跨域中间件。"""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(get_settings().cors.allow_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Conversation-ID", "X-Thread-ID", "Content-Disposition"],
    )


def trusted_host_middleware(app: FastAPI) -> None:
    """注册可信 Host 中间件。"""
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])


def correlation_id_middleware(app: FastAPI) -> None:
    """注册请求链路 ID 中间件。"""
    app.add_middleware(CorrelationIdMiddleware)
