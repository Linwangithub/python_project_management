"""令牌 Schema 模块，定义登录认证返回的 token 数据结构。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from pydantic import BaseModel, Field

from app.schemas import base


class Token(BaseModel):
    """访问令牌数据模型。"""
    access_token: str = Field(..., description='令牌')
    token_type: str = Field('Bearer', description='令牌类型')


class TokenResponse(base.BaseResponse):
    """访问令牌统一响应模型。"""
    data: Token


class TokenPayload(BaseModel):
    """JWT 令牌载荷模型。"""
    sub: int


class TokenPayloadResponse(base.BaseResponse):
    """JWT 令牌载荷统一响应模型。"""
    data: TokenPayload


class LoginData(BaseModel):
    """登录成功返回数据模型。

    包含访问令牌和权限快照。
    """
    token: Token = Field(..., description='令牌信息')
    permissions: dict = Field(default_factory=dict, description='权限快照')


class LoginResponse(base.BaseResponse):
    """登录成功统一响应模型。"""
    data: LoginData
