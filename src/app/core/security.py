"""安全模块，负责密码哈希、JWT 令牌创建和认证相关工具。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from datetime import datetime, timedelta, timezone
from typing import Any
import jwt
from passlib.context import CryptContext
from app.core.deps import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def create_access_token(subject: str | Any, expires_delta: timedelta) -> str:
    """创建 JWT 访问令牌。

    写入过期时间和用户标识后使用配置密钥签名。
    """
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode = {"exp": expire, "sub": str(subject)}
    encoded_jwt = jwt.encode(to_encode, get_settings().api.auth0.secret_key, algorithm=get_settings().api.auth0.algorithm)
    return encoded_jwt


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """校验明文密码和密码哈希是否匹配。"""
    salt = get_settings().dev.salt
    if salt:
        plain_password = f"{salt}{plain_password}"
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """生成密码哈希。

    保存用户密码前调用。
    """
    salt = get_settings().dev.salt
    if salt:
        password = f"{salt}{password}"
    return pwd_context.hash(password)
