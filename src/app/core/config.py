"""应用配置定义模块。"""

import logging
import secrets
from typing import Literal

from pydantic import BaseModel, SecretStr, field_validator, model_validator
from pydantic_core import MultiHostUrl
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.utils.pspm.project_config import ROOT_PROJECT_BASE_DIR, USER_PROJECT_BASE_PATH_TEMPLATE

logger = logging.getLogger(__name__)


class SettingsDev(BaseModel):
    """开发调试相关配置。"""

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
        """把空字符串形式的 OpenAPI 地址转换为 None。"""
        if value == "":
            return None
        return value


class SettingsProjectPaths(BaseModel):
    """不同用户角色对应的项目默认路径配置。"""

    root_base_path: str = ROOT_PROJECT_BASE_DIR
    user_base_path_template: str = USER_PROJECT_BASE_PATH_TEMPLATE

    model_config = SettingsConfigDict(frozen=True)

    @field_validator("root_base_path")
    @classmethod
    def check_root_base_path(cls, value: str) -> str:
        """
        校验 root 角色默认项目根路径。
        
        参数：
        - value：环境变量或默认配置传入的路径。
        
        返回：
        - 去除末尾斜杠后的绝对路径。
        """
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
        """
        校验普通用户项目根路径模板。
        
        参数：
        - value：包含 `{username}` 占位符的路径模板。
        
        返回：
        - 去除末尾斜杠后的模板路径。
        """
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
    """JWT 和认证相关配置。"""

    domain: SecretStr | None = None
    audience: SecretStr | None = None
    algorithm: str = "HS256"
    secret_key: str = secrets.token_urlsafe(32)
    client_domain: SecretStr | None = None
    client_id: SecretStr | None = None
    client_secret: SecretStr | None = None
    access_token_expire_minutes: int = 60 * 24 * 90


class SettingsAPI(BaseModel):
    """API 路由和文档相关配置。"""

    auth0: SettingsAuth0 = SettingsAuth0()

    model_config = SettingsConfigDict(frozen=True, extra="ignore")


class SettingsCORS(BaseModel):
    """CORS middleware settings.

    allow_origins 默认用于本地开发和历史联调环境；生产环境可通过环境变量覆盖。
    """

    allow_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://192.168.93.129:5173",
        "http://192.168.93.129:8000",
        "http://192.168.93.129:8888",
    ]

    model_config = SettingsConfigDict(frozen=True)


class SettingsS3(BaseModel):
    """兼容 S3 协议的对象存储配置。"""

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
    """AWS/S3 对象存储聚合配置。"""

    s3: SettingsS3 = SettingsS3()

    model_config = SettingsConfigDict(frozen=True)


class SettingsDB(BaseModel):
    """异步数据库连接配置。"""

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
        """生成数据库连接 URI。"""
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
    """异步 Redis 连接配置。"""

    scheme: str = "redis"
    host: str | None = None
    username: str | None = None
    password: SecretStr | None = None
    database: int | None = 0
    prefix: str | None = ""

    model_config = SettingsConfigDict(frozen=True)

    @property
    def uri(self) -> MultiHostUrl | None:
        """生成 Redis 连接 URI；未配置 Redis 时返回 None。"""
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
    """从环境变量和 .env 文件加载的完整应用配置。"""

    api: SettingsAPI = SettingsAPI()
    aws: SettingsAWS = SettingsAWS()
    dev: SettingsDev = SettingsDev()
    db: SettingsDB = SettingsDB()
    redis: SettingsRedis = SettingsRedis()
    project_paths: SettingsProjectPaths = SettingsProjectPaths()
    cors: SettingsCORS = SettingsCORS()

    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        frozen=True,
        extra="ignore",
    )

    @model_validator(mode="after")
    def check(self) -> "Settings":
        """校验应用配置项是否满足启动要求。"""
        return self
