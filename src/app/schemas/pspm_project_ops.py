"""项目管理 Schema 模块，定义项目、终端、同步、设置等接口请求响应结构。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from datetime import datetime
from typing import Any, List, Literal

from pydantic import BaseModel, Field

from app.schemas import base

class ProjectCopyRequest(BaseModel):
    """复制项目请求体。"""
    target_server_ip: str = Field(..., description='目标服务器IP')
    target_dir: str = Field(..., description='目标目录')


class ProjectExportRequest(BaseModel):
    """导出项目请求体。"""
    target_dir: str = Field(..., description='导出目录')
