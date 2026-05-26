"""登录注册接口模块，处理账号登录、OAuth2 登录和注册请求。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

import logging
from typing import Annotated
from datetime import timedelta

from fastapi import APIRouter, Depends, Request, HTTPException, Body
from fastapi.security import OAuth2PasswordRequestForm

from app.core.deps import SessionDep, get_settings
from app.core import security
from app import crud, schemas

logger = logging.getLogger(__name__)

router = APIRouter()


class LoginRequestData:
    """登录请求参数对象。

    由 FastAPI Depends 注入，包含用户名和密码。
    """
    def __init__(self, username: str = Body(..., description='用户名'), password: str = Body(..., description='密码')):
        """初始化登录请求参数。

        Args:
            username: 前端登录表单提交的用户名。
            password: 前端登录表单提交的明文密码，后续由认证逻辑校验。
        """
        self.username = username
        self.password = password


class RegisterRequestData:
    """注册请求参数对象。

    由 FastAPI Depends 注入，包含用户名和密码。
    """
    def __init__(self, username: str = Body(..., description='用户名'), password: str = Body(..., description='密码')):
        """初始化注册请求参数。

        Args:
            username: 前端注册表单提交的新用户名。
            password: 前端注册表单提交的明文密码，创建用户时会加密保存。
        """
        self.username = username
        self.password = password


@router.post('/oauth-login', name='登录', response_model=schemas.token.Token)
async def oauth_login(
    request: Request,
    session: SessionDep,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> schemas.token.Token:
    """OAuth2 表单登录接口。

    校验账号、密码和用户状态，成功后返回访问令牌。
    """
    user = await crud.users.get(session, obj_in={'username': form_data.username})
    if not user:
        raise HTTPException(status_code=400, detail='该账号不存在，请先注册')

    auth_user = await crud.users.authenticate(session, username=form_data.username, password=form_data.password)
    if not auth_user:
        raise HTTPException(status_code=400, detail='密码错误')

    if not await crud.users.is_active(auth_user):
        raise HTTPException(status_code=400, detail='用户已禁用')

    access_token_expires = timedelta(minutes=get_settings().api.auth0.access_token_expire_minutes)
    return schemas.token.Token(
        access_token=security.create_access_token(auth_user.id, expires_delta=access_token_expires)
    )


@router.post('/login', name='登录', response_model=schemas.token.TokenResponse)
async def login(
    request: Request,
    session: SessionDep,
    form_data: Annotated[LoginRequestData, Depends()],
) -> schemas.token.TokenResponse:
    """普通登录接口。

    接收用户名和密码，校验通过后返回统一响应格式的访问令牌。
    """
    user = await crud.users.get(session, obj_in={'username': form_data.username})
    if not user:
        raise HTTPException(status_code=400, detail='该账号不存在，请求注册')

    auth_user = await crud.users.authenticate(session, username=form_data.username, password=form_data.password)
    if not auth_user:
        raise HTTPException(status_code=400, detail='密码错误')

    if not await crud.users.is_active(auth_user):
        raise HTTPException(status_code=400, detail='用户已禁用')

    access_token_expires = timedelta(minutes=get_settings().api.auth0.access_token_expire_minutes)
    return schemas.token.TokenResponse(
        data=schemas.token.Token(
            access_token=security.create_access_token(auth_user.id, expires_delta=access_token_expires)
        )
    )


@router.post('/register', name='注册', response_model=schemas.base.BaseResponse)
async def register(
    request: Request,
    session: SessionDep,
    form_data: Annotated[RegisterRequestData, Depends()],
) -> schemas.base.BaseResponse:
    """注册模块组件或处理用户注册。

    在路由模块中用于创建用户并绑定默认角色；在核心模块中用于注册应用能力。
    """
    username = (form_data.username or '').strip()
    password = (form_data.password or '').strip()

    if not username:
        raise HTTPException(status_code=400, detail='账号不能为空')
    if not password:
        raise HTTPException(status_code=400, detail='密码不能为空')

    exists = await crud.users.get(session, obj_in={'username': username})
    if exists:
        raise HTTPException(status_code=400, detail='账号已存在，请直接登录')

    next_userid = await crud.users.next_userid(session)
    user = await crud.users.create(
        session,
        obj_in={
            'userid': next_userid,
            'username': username,
            'password': password,
        },
    )

    user_role = await crud.rbac.get_role_by_key(session, role_key='user')
    if not user_role:
        raise HTTPException(status_code=500, detail='默认角色不存在，请联系管理员初始化RBAC')

    await crud.rbac.bind_user_role(session, user_id=user.id, role_id=user_role.id)
    return schemas.base.BaseResponse(message='注册成功')
