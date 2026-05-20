from datetime import datetime, timedelta, timezone
from typing import Any
import jwt
from passlib.context import CryptContext
from app.core.deps import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def create_access_token(subject: str | Any, expires_delta: timedelta) -> str:
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode = {"exp": expire, "sub": str(subject)}
    encoded_jwt = jwt.encode(to_encode, get_settings().api.auth0.secret_key, algorithm=get_settings().api.auth0.algorithm)
    return encoded_jwt


def verify_password(plain_password: str, hashed_password: str) -> bool:
    salt = get_settings().dev.salt
    if salt:
        plain_password = f"{salt}{plain_password}"
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    salt = get_settings().dev.salt
    if salt:
        password = f"{salt}{password}"
    return pwd_context.hash(password)
