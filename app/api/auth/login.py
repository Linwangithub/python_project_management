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
    def __init__(self, username: str = Body(..., description='用户名'), password: str = Body(..., description='密码')):
        self.username = username
        self.password = password


class RegisterRequestData:
    def __init__(self, username: str = Body(..., description='用户名'), password: str = Body(..., description='密码')):
        self.username = username
        self.password = password


@router.post('/oauth-login', name='登录', response_model=schemas.token.Token)
async def oauth_login(
    request: Request,
    session: SessionDep,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> schemas.token.Token:
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
