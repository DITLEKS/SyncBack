"""
Единая точка чтения конфигурации приложения.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Literal["local", "staging", "production"] = "local"
    debug: bool = False

    database_url: str

    redis_url: str

    minio_endpoint: str
    minio_root_user: str
    minio_root_password: str
    minio_bucket: str
    minio_secure: bool = False
    minio_presigned_url_expire_seconds: int = 300

    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60

    login_max_attempts: int = 5
    login_lockout_seconds: int = 300

    max_upload_size_mb: int = 50

    llm_provider: Literal["stub", "remote_http", "onprem"] = "stub"
    llm_endpoint: str = ""
    llm_api_key: str = ""
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 3

    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
