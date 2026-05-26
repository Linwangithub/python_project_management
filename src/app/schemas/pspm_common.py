"""项目管理 Schema 模块，定义项目、终端、同步、设置等接口请求响应结构。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from datetime import datetime
from typing import Any, List, Literal

from pydantic import BaseModel, Field

from app.schemas import base

RoleName = Literal['root', 'user']
ProjectStatusName = Literal['运行中', '已停止', '创建中', '创建成功', '创建失败']
ProjectDeleteScope = Literal['project_only', 'project_and_conda', 'project_conda_and_db', 'project_conda_nginx', 'project_conda_db_nginx']

