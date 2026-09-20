"""
Единая точка чтения конфигурации приложения.
"""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
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

    # CORS: строка, разделённая запятыми, или одиночное значение.
    # Примеры:
    #   CORS_ALLOWED_ORIGINS=*                              (только для local)
    #   CORS_ALLOWED_ORIGINS=http://localhost:5173          (один origin)
    #   CORS_ALLOWED_ORIGINS=https://app.example.com,https://staging.example.com
    cors_allowed_origins: list[str] = ["*"]

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v: object) -> list[str]:
        """Принимает строку (из .env) или уже готовый список."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v  # type: ignore[return-value]

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
