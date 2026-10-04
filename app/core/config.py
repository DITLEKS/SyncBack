"""
Единая точка чтения конфигурации приложения.
"""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Literal["local", "staging", "production"] = "local"
    debug: bool = False

    database_url: str

    redis_url: str

    redis_sse_channel: str = "syncscribe:sse"

    minio_endpoint: str
    # Приложению нужен сервисный пользователь с правами только на свой бакет.
    # Старые имена переменных (MINIO_ROOT_*) принимаются для совместимости.
    minio_access_key: str = Field(
        validation_alias=AliasChoices("MINIO_ACCESS_KEY", "MINIO_ROOT_USER")
    )
    minio_secret_key: str = Field(
        validation_alias=AliasChoices("MINIO_SECRET_KEY", "MINIO_ROOT_PASSWORD")
    )
    minio_bucket: str
    minio_secure: bool = False
    minio_presigned_url_expire_seconds: int = 300

    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    # REVIEW-2: вынесено из магической константы 30*24*60*60 в jwt_handler.py
    jwt_refresh_token_expire_days: int = 30

    login_max_attempts: int = 5
    login_lockout_seconds: int = 300

    # Пул соединений API-процесса. Пул на каждый процесс gunicorn, поэтому
    # суммарное число соединений = workers × (pool_size + max_overflow);
    # оно должно укладываться в max_connections PostgreSQL.
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout_seconds: int = 30
    db_pool_recycle_seconds: int = 1800

    # Хранилище счётчиков slowapi. memory:// считает лимиты отдельно в каждом
    # процессе gunicorn, поэтому вне локальной разработки нужен Redis
    # (например, redis://redis:6379/1 — отдельная база от очереди и SSE).
    rate_limit_storage_uri: str = "memory://"

    max_upload_size_mb: int = 50

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    llm_provider: Literal["stub", "remote_http", "onprem"] = "stub"
    llm_endpoint: str = ""
    llm_api_key: str = ""
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 3
    # Документ + источник в символах; запас под контекст модели и ответ.
    llm_max_input_chars: int = 200_000

    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"

    # NoDecode: pydantic-settings иначе пытается разобрать значение как JSON
    # и падает на обычной строке «a,b» ещё до валидатора.
    cors_allowed_origins: Annotated[list[str], NoDecode]

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v: object) -> list[str]:
        """Принимает строку с origin через запятую или готовый список."""
        if isinstance(v, str):
            v = [origin.strip() for origin in v.split(",") if origin.strip()]
        if not isinstance(v, list):
            raise ValueError("cors_allowed_origins должен быть списком origin")
        return v

    @field_validator("cors_allowed_origins")
    @classmethod
    def _forbid_wildcard_origin(cls, v: list[str]) -> list[str]:
        # CORS включён с allow_credentials=True; в этом режиме «*» заставляет
        # Starlette отражать любой Origin, и cookie-сессии становятся доступны
        # произвольному сайту.
        if "*" in v:
            raise ValueError(
                "cors_allowed_origins не может содержать «*»: укажите явные origin фронтенда"
            )
        if not v:
            raise ValueError("cors_allowed_origins не может быть пустым")
        return v

    # Адреса reverse proxy, чьим заголовкам X-Forwarded-For/-Proto можно верить.
    # Пусто — заголовки игнорируются, клиентом считается прямое соединение.
    trusted_proxy_hosts: Annotated[list[str], NoDecode] = []

    @field_validator("trusted_proxy_hosts", mode="before")
    @classmethod
    def _parse_trusted_proxies(cls, v: object) -> list[str]:
        if isinstance(v, str):
            v = [host.strip() for host in v.split(",") if host.strip()]
        if not isinstance(v, list):
            raise ValueError("trusted_proxy_hosts должен быть списком адресов")
        if "*" in v:
            raise ValueError(
                "trusted_proxy_hosts не может содержать «*»: тогда любой клиент "
                "подменит свой адрес заголовком X-Forwarded-For"
            )
        return v

    # M-3: jwt_secret обязан быть не менее 32 символов.
    # Слабый секрет (например, "secret" или пустая строка) позволяет
    # тривиально форжировать токены.
    @field_validator("jwt_secret")
    @classmethod
    def _validate_jwt_secret(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError(
                "jwt_secret должен содержать минимум 32 символа. "
                "Сгенерируйте его командой: openssl rand -hex 32"
            )
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
