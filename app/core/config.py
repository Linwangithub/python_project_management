"""Configuration."""

import logging
import secrets
from typing import Literal

from pydantic import BaseModel, SecretStr, field_validator, model_validator
from pydantic_core import MultiHostUrl
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class SettingsDev(BaseModel):
    """Developer settings."""

    environment: str = "development"
    project_name: str = "FastAPI Template"
    api_str: str = "/api"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    openapi_url: str | None = "/openapi.json"
    salt: str | None = None

    model_config = SettingsConfigDict(frozen=True)

    @field_validator("openapi_url")
    @classmethod
    def check_openapi_url(cls, value: str | None) -> str | None:
        """Convert empty string to None."""
        if value == "":
            return None
        return value


class SettingsProjectPaths(BaseModel):
    """Project path defaults for different roles."""

    root_base_path: str = "/root/project"
    user_base_path_template: str = "/home/{username}/project"

    model_config = SettingsConfigDict(frozen=True)

    @field_validator("root_base_path")
    @classmethod
    def check_root_base_path(cls, value: str) -> str:
        path = (value or "").strip()
        if not path:
            raise ValueError("project root base path cannot be empty")
        if not path.startswith("/"):
            raise ValueError("project root base path must be absolute")
        if path != "/":
            path = path.rstrip("/")
        return path

    @field_validator("user_base_path_template")
    @classmethod
    def check_user_base_path_template(cls, value: str) -> str:
        template = (value or "").strip()
        if not template:
            raise ValueError("project user base path template cannot be empty")
        if "{username}" not in template:
            raise ValueError("project user base path template must contain {username}")
        sample = template.replace("{username}", "demo")
        if not sample.startswith("/"):
            raise ValueError("project user base path template must resolve to absolute path")
        if template != "/":
            template = template.rstrip("/")
        return template


class SettingsAuth0(BaseModel):
    """JWT/Auth settings."""

    domain: SecretStr | None = None
    audience: SecretStr | None = None
    algorithm: str = "HS256"
    secret_key: str = secrets.token_urlsafe(32)
    client_domain: SecretStr | None = None
    client_id: SecretStr | None = None
    client_secret: SecretStr | None = None
    access_token_expire_minutes: int = 60 * 24 * 90


class SettingsAPI(BaseModel):
    """API settings."""

    auth0: SettingsAuth0 = SettingsAuth0()

    model_config = SettingsConfigDict(frozen=True, extra="ignore")


class SettingsS3(BaseModel):
    """S3-compatible object storage settings."""

    bucket: str = ""
    endpoint: str | None = None
    access_key: str | None = None
    secret_key: str | None = None
    secure: bool = True
    presigned_post_expiration: int = 360
    presigned_download_expiration: int = 360
    presigned_post_max_bytes: int = 50 * int(1e6)

    model_config = SettingsConfigDict(frozen=True)


class SettingsAWS(BaseModel):
    """AWS/S3 settings."""

    s3: SettingsS3 = SettingsS3()

    model_config = SettingsConfigDict(frozen=True)


class SettingsDB(BaseModel):
    """Async database settings."""

    scheme: str = "mysql+aiomysql"
    host: str | None = None
    username: str | None = None
    password: SecretStr | None = None
    database: str | None = None
    prefix: str | None = None
    timezone: str = "Asia/Shanghai"

    model_config = SettingsConfigDict(frozen=True)

    @property
    def uri(self) -> MultiHostUrl:
        """Generate the database URI."""
        if self.host is None or self.username is None or self.password is None or self.database is None:
            raise ValueError("'host', 'username', 'password', and 'database' must be supplied.")
        return MultiHostUrl.build(
            scheme=self.scheme,
            username=self.username,
            password=self.password.get_secret_value(),
            host=self.host,
            path=self.database,
        )


class SettingsRedis(BaseModel):
    """Async Redis settings."""

    scheme: str = "redis"
    host: str | None = None
    username: str | None = None
    password: SecretStr | None = None
    database: int | None = 0
    prefix: str | None = ""

    model_config = SettingsConfigDict(frozen=True)

    @property
    def uri(self) -> MultiHostUrl | None:
        """Generate the Redis URI. Return None when Redis is not configured."""
        if self.host is None:
            return None
        return MultiHostUrl.build(
            scheme=self.scheme,
            host=self.host,
            username=self.username,
            password=self.password.get_secret_value() if self.password else None,
            path=f"{self.database}",
        )


class Settings(BaseSettings):
    """All settings loaded from environment variables and .env."""

    api: SettingsAPI = SettingsAPI()
    aws: SettingsAWS = SettingsAWS()
    dev: SettingsDev = SettingsDev()
    db: SettingsDB = SettingsDB()
    redis: SettingsRedis = SettingsRedis()
    project_paths: SettingsProjectPaths = SettingsProjectPaths()

    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        frozen=True,
        extra="ignore",
    )

    @model_validator(mode="after")
    def check(self) -> "Settings":
        """Validate settings."""
        return self
