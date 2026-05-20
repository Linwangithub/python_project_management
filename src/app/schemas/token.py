from pydantic import BaseModel, Field

from app.schemas import base


class Token(BaseModel):
    access_token: str = Field(..., description='令牌')
    token_type: str = Field('Bearer', description='令牌类型')


class TokenResponse(base.BaseResponse):
    data: Token


class TokenPayload(BaseModel):
    sub: int


class TokenPayloadResponse(base.BaseResponse):
    data: TokenPayload


class LoginData(BaseModel):
    token: Token = Field(..., description='令牌信息')
    permissions: dict = Field(default_factory=dict, description='权限快照')


class LoginResponse(base.BaseResponse):
    data: LoginData
