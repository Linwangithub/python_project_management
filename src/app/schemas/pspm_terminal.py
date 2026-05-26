"""项目管理 Schema 模块，定义项目、终端、同步、设置等接口请求响应结构。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from datetime import datetime
from typing import Any, List, Literal

from pydantic import BaseModel, Field

from app.schemas import base

class TerminalServerOption(BaseModel):
    """终端可连接服务器选项。"""
    server_id: int = Field(..., description='服务器ID')
    ip: str = Field(..., description='服务器IP')
    alias: str | None = Field(None, description='服务器别名')
    ssh_port: int = Field(22, description='SSH端口')


class TerminalServerOptionsResponse(base.BaseResponse):
    """终端可连接服务器列表接口响应。"""
    data: List[TerminalServerOption] = Field(default_factory=list, description='终端服务器选项')


class TerminalSessionCreate(BaseModel):
    """创建终端会话请求体。"""
    server_ip: str = Field(..., description='服务器IP')
    alias: str = Field(..., description='会话别名')


class TerminalSessionInfo(BaseModel):
    """终端会话信息。"""
    session_id: str = Field(..., description='会话ID')
    server_ip: str = Field(..., description='服务器IP')
    alias: str = Field(..., description='会话别名')
    cwd: str = Field(..., description='当前工作目录')
    prompt: str = Field(..., description='命令提示符文本')
    welcome_message: str = Field('连接成功！', description='欢迎消息')


class TerminalSessionCreateResponse(base.BaseResponse):
    """创建终端会话接口响应。"""
    data: TerminalSessionInfo


class TerminalSessionClose(BaseModel):
    """关闭终端会话请求体。"""
    session_id: str = Field(..., description='会话ID')


class TerminalExecuteRequest(BaseModel):
    """执行终端命令请求体。"""
    session_id: str = Field(..., description='会话ID')
    command: str = Field(..., description='命令')
    mode: str | None = Field('', description='执行模式')



class ProjectForegroundFinalize(BaseModel):
    """前台启动完成确认请求体。"""
    project_id: int = Field(..., description='项目ID')
    pid: str = Field(..., description='已启动进程PID')
    port: str | None = Field('', description='检测到或配置的端口')
    log_file: str | None = Field('', description='当前启动日志文件')


class TerminalCompleteRequest(BaseModel):
    """终端命令自动补全请求体。"""
    session_id: str = Field(..., description='会话ID')
    command: str = Field(..., description='Tab补全前的命令输入')


class TerminalCompleteResult(BaseModel):
    """终端命令自动补全结果数据。"""
    session_id: str = Field(..., description='会话ID')
    original_command: str = Field(..., description='原始命令')
    completed_command: str = Field(..., description='补全后的命令')
    candidates: List[str] = Field(default_factory=list, description='补全候选项')
    cwd: str = Field(..., description='当前工作目录')
    message: str = Field('ok', description='消息')


class TerminalCompleteResponse(base.BaseResponse):
    """终端命令自动补全接口响应。"""
    data: TerminalCompleteResult


class TerminalExecuteResult(BaseModel):
    """终端命令执行结果数据。"""
    session_id: str = Field(..., description='会话ID')
    command: str = Field(..., description='命令')
    cwd: str = Field(..., description='当前工作目录')
    prompt_before: str = Field(..., description='执行前提示符')
    prompt_after: str = Field(..., description='执行后提示符')
    exit_code: int = Field(0, description='退出码')
    stdout: str = Field('', description='标准输出')
    stderr: str = Field('', description='标准错误')
    blocked: bool = Field(False, description='是否被策略拦截')
    message: str = Field('ok', description='消息')


class TerminalExecuteResponse(base.BaseResponse):
    """终端命令执行接口响应。"""
    data: TerminalExecuteResult
