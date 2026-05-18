from typing import Optional

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PspmEnv(Base):
    """环境管理表。

    用途：
    - 保存用户创建或关联的 Conda 环境信息。
    - 被环境管理列表和项目管理环境字段复用。
    """
    owner_id: Mapped[int] = mapped_column(Integer, index=True, comment="所属用户ID")
    env_name: Mapped[str] = mapped_column(String(128), index=True, comment="环境名称")
    project_name: Mapped[Optional[str]] = mapped_column(String(128), default=None, comment="关联项目名称")
    python_version: Mapped[Optional[str]] = mapped_column(String(32), default=None, comment="Python版本")
    main_packages: Mapped[Optional[str]] = mapped_column(Text, default=None, comment="主包与版本信息")
    remark: Mapped[Optional[str]] = mapped_column(String(255), default=None, comment="备注")
    status: Mapped[int] = mapped_column(Integer, default=1, index=True, comment="状态：-1删除，1正常")
    created_by: Mapped[int] = mapped_column(Integer, default=1, comment="创建人ID")


class PspmServer(Base):
    """服务器管理表。

    用途：
    - 保存服务器 IP、root 密码、已分配用户和备注信息。
    - 被服务器管理、项目创建、终端会话等流程查询。
    """
    alias: Mapped[Optional[str]] = mapped_column(String(128), default=None, comment="服务器别名")
    ip: Mapped[str] = mapped_column(String(64), index=True, comment="服务器IP")
    ssh_port: Mapped[int] = mapped_column(Integer, default=22, comment="SSH端口")
    root_password: Mapped[Optional[str]] = mapped_column(String(255), default=None, comment="root密码（明文）")
    assigned_users: Mapped[Optional[str]] = mapped_column(Text, default='root', comment="已分配用户（逗号分隔）")
    middlewares: Mapped[Optional[str]] = mapped_column(Text, default=None, comment="中间件信息")
    heartbeat: Mapped[Optional[str]] = mapped_column(String(64), default=None, comment="心跳时间")
    remark: Mapped[Optional[str]] = mapped_column(String(255), default=None, comment="备注")
    status: Mapped[int] = mapped_column(Integer, default=1, index=True, comment="状态：-1删除，1正常")
    created_by: Mapped[int] = mapped_column(Integer, default=1, comment="创建人ID")


class PspmServerUser(Base):
    """服务器用户关系表。

    用途：
    - 保存服务器与系统用户之间的授权关系。
    - 用于判断普通用户可使用哪些业务服务器。
    """
    server_id: Mapped[int] = mapped_column(Integer, index=True, comment="服务器ID")
    user_id: Mapped[int] = mapped_column(Integer, index=True, comment="用户ID")
    status: Mapped[int] = mapped_column(Integer, default=1, index=True, comment="状态：-1删除，1正常")
    created_by: Mapped[int] = mapped_column(Integer, default=1, comment="创建人ID")


class PspmProject(Base):
    """项目管理主表。

    用途：
    - 保存项目目录、Conda 环境、数据库、Nginx、启动命令和运行状态。
    - 被项目列表、创建项目、设置项目、启动停止、删除项目等流程使用。
    """
    owner_id: Mapped[int] = mapped_column(Integer, index=True, comment="项目所属用户ID")
    server_id: Mapped[Optional[int]] = mapped_column(Integer, default=None, index=True, comment="服务器ID")
    env_id: Mapped[Optional[int]] = mapped_column(Integer, default=None, index=True, comment="环境ID")

    name: Mapped[str] = mapped_column(String(128), index=True, comment="项目名称")
    description: Mapped[Optional[str]] = mapped_column(String(255), default=None, comment="项目描述")

    backend_path: Mapped[Optional[str]] = mapped_column(Text, default=None, comment="后端代码路径")
    frontend_path: Mapped[Optional[str]] = mapped_column(Text, default=None, comment="前端代码路径")
    nginx_conf_path: Mapped[Optional[str]] = mapped_column(Text, default=None, comment="Nginx配置路径")
    nginx_server_ip: Mapped[Optional[str]] = mapped_column(String(64), default=None, comment="Nginx服务器IP")
    nginx_config_text: Mapped[Optional[str]] = mapped_column(Text, default=None, comment="Nginx详细配置文本")

    frontend_port: Mapped[Optional[str]] = mapped_column(String(16), default=None, comment="Nginx前端端口")
    backend_dev_port: Mapped[Optional[str]] = mapped_column(String(16), default=None, comment="后端开发端口")
    backend_deploy_port: Mapped[Optional[str]] = mapped_column(String(16), default=None, comment="后端部署端口")

    database_name: Mapped[Optional[str]] = mapped_column(String(128), default=None, comment="数据库名")
    database_host: Mapped[Optional[str]] = mapped_column(String(128), default=None, comment="Database host")
    database_port: Mapped[Optional[str]] = mapped_column(String(16), default=None, comment="Database port")
    database_user: Mapped[Optional[str]] = mapped_column(String(128), default=None, comment="Database user")
    database_password: Mapped[Optional[str]] = mapped_column(String(255), default=None, comment="Database password")
    conda_env_name: Mapped[Optional[str]] = mapped_column(String(128), default=None, comment="Conda环境名称")
    python_version: Mapped[Optional[str]] = mapped_column(String(32), default=None, comment="Python版本")

    dev_start_command: Mapped[Optional[str]] = mapped_column(Text, default=None, comment="开发启动命令")
    deploy_start_command: Mapped[Optional[str]] = mapped_column(Text, default=None, comment="部署启动命令")
    entry_file_path: Mapped[Optional[str]] = mapped_column(Text, default=None, comment="项目入口文件位置")

    status: Mapped[int] = mapped_column(Integer, default=0, index=True, comment="状态：0已停止，1运行中")
    auto_start: Mapped[int] = mapped_column(Integer, default=0, comment="是否开机自启：0否，1是")

    remark: Mapped[Optional[str]] = mapped_column(String(255), default=None, comment="备注")
    created_by: Mapped[int] = mapped_column(Integer, default=1, comment="创建人ID")


class PspmProjectOperationLog(Base):
    """项目操作日志表。

    用途：
    - 记录项目创建、设置修改、启动、停止、复制、导出等操作。
    - 日志按钮通过该表展示“什么时间、什么人、把什么配置从什么改成什么”。
    """
    project_id: Mapped[int] = mapped_column(Integer, index=True, comment="项目ID")
    operator_id: Mapped[Optional[int]] = mapped_column(Integer, default=None, index=True, comment="操作人ID")
    operator_name: Mapped[Optional[str]] = mapped_column(String(128), default=None, comment="操作人账号")
    action: Mapped[str] = mapped_column(String(64), index=True, comment="操作编码")
    action_label: Mapped[str] = mapped_column(String(128), comment="操作名称")
    summary: Mapped[Optional[str]] = mapped_column(String(255), default=None, comment="操作摘要")
    before_data: Mapped[Optional[str]] = mapped_column(Text, default=None, comment="操作前配置JSON")
    after_data: Mapped[Optional[str]] = mapped_column(Text, default=None, comment="操作后配置JSON")
    detail: Mapped[Optional[str]] = mapped_column(Text, default=None, comment="操作详细信息JSON")
    status: Mapped[int] = mapped_column(Integer, default=1, index=True, comment="状态：-1删除，1正常")
    created_by: Mapped[int] = mapped_column(Integer, default=1, comment="创建人ID")

