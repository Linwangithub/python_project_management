#!/usr/bin/python
# -*- coding: utf-8 -*-
"""后端服务启动入口。

本模块负责解析命令行 host/port 参数，并使用 uvicorn 启动 FastAPI 应用。
默认监听地址和端口来自 app.utils.pspm.project_config，避免入口脚本硬编码运行参数。
"""

import argparse

import uvicorn

from app import create_app
from app.core.deps import get_settings
from app.utils.pspm.project_config import DEFAULT_API_HOST, DEFAULT_API_PORT

create_app = create_app()


def get_parser():
    """解析后端启动命令行参数。

    Returns:
        argparse.Namespace: 包含 host、port 和剩余 args 的命令行参数对象。
    """
    parser = argparse.ArgumentParser(description='Python 项目管理平台后端服务启动入口')
    parser.add_argument('args', nargs='*', help='预留参数，兼容历史启动方式')
    parser.add_argument('-host', '--host', nargs='?', default=DEFAULT_API_HOST, type=str, help='后端服务监听地址')
    parser.add_argument('-port', '--port', nargs='?', default=DEFAULT_API_PORT, type=int, help='后端服务监听端口')
    return parser.parse_args()


if __name__ == '__main__':
    parser = get_parser()
    uvicorn.run(
        app='main:create_app',
        workers=1,
        host=parser.host,
        port=parser.port,
        forwarded_allow_ips='*',
        reload=get_settings().dev.environment != 'production',
    )
