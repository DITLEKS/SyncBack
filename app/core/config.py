"""Единая точка чтения конфигурации приложения."""

from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import unquote, urlsplit

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_WEAK_SECRET_VALUES = {
    "admin",
    "change-me",
    "changeme",
    "default",
    "minioadmin",
    "password",
    "postgres",
    "root",
    "secret",
    "syncscribe",
    "test",
}
_WEAK_SECRET_PREFIXES = (
    "change-me",
    "changeme",
    "replace-me",
    "replace_with",
    "your-",
    "your_",
    "<",
)


def _is_placeholder_secret(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    return (
        normalized in _WEAK_SECRET_VALUES
        or normalized.startswith(_WEAK_SECRET_PREFIXES)
        or normalized.endswith("...")
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Literal["local", "staging", "production"] = "local"
    debug: bool = False

    database_url: str
    redis_url: str
    redis_sse_channel: str = "syncscribe:sse"

    s3_endpoint: str = Field(validation_alias=AliasChoices("S3_ENDPOINT", "MINIO_ENDPOINT"))
    s3_access_key: str = Field(
        validation_alias=AliasChoices("S3_ACCESS_KEY", "MINIO_ACCESS_KEY", "MINIO_ROOT_USER")
    )
    s3_secret_key: str = Field(
        validation_alias=AliasChoices("S3_SECRET_KEY", "MINIO_SECRET_KEY", "MINIO_ROOT_PASSWORD")
    )
    s3_bucket: str = Field(validation_alias=AliasChoices("S3_BUCKET", "MINIO_BUCKET"))
    s3_secure: bool = Field(
        default=False, validation_alias=AliasChoices("S3_SECURE", "MINIO_SECURE")
    )
    s3_presigned_url_expire_seconds: int = Field(
        default=300,
        validation_alias=AliasChoices(
            "S3_PRESIGNED_URL_EXPIRE_SECONDS", "MINIO_PRESIGNED_URL_EXPIRE_SECONDS"
        ),
    )

    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 30

    login_max_attempts: int = 5
    login_lockout_seconds: int = 300

    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout_seconds: int = 30
    db_pool_recycle_seconds: int = 1800

    rate_limit_storage_uri: str = "memory://"
    max_upload_size_mb: int = 50

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    llm_provider: Literal["stub", "openai_compatible", "remote_http", "onprem"] = "stub"
    llm_endpoint: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    llm_auth: Literal["none", "bearer", "oauth_gigachat"] = "bearer"
    llm_oauth_url: str = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    llm_oauth_scope: str = "GIGACHAT_API_PERS"
    llm_ca_bundle: str = ""
    llm_verify_ssl: bool = True
    llm_temperature: float = 0.1
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 3
    llm_max_input_chars: int = 200_000

    @model_validator(mode="after")
    def _validate_llm(self) -> "Settings":
        if self.llm_provider == "stub":
            return self
        if not self.llm_endpoint:
            raise ValueError("LLM_ENDPOINT обязателен для LLM_PROVIDER != stub")
        if not self.llm_model:
            raise ValueError(
                "LLM_MODEL обязателен: у OpenAI-совместимых API нет модели по умолчанию"
            )
        if self.llm_auth != "none" and not self.llm_api_key:
            raise ValueError(f"LLM_API_KEY обязателен для LLM_AUTH={self.llm_auth}")
        return self

    @model_validator(mode="after")
    def _forbid_deployment_placeholders(self) -> "Settings":
        """Внешние окружения не должны запускаться с примерами из .env.example."""
        if self.env == "local":
            return self

        unsafe_fields: list[str] = []
        try:
            database_password = unquote(urlsplit(self.database_url).password or "")
        except ValueError:
            database_password = ""
        if _is_placeholder_secret(database_password):
            unsafe_fields.append("DATABASE_URL password")
        if _is_placeholder_secret(self.jwt_secret):
            unsafe_fields.append("JWT_SECRET")
        if _is_placeholder_secret(self.s3_secret_key):
            unsafe_fields.append("S3_SECRET_KEY")
        if (
            self.llm_provider != "stub"
            and self.llm_auth != "none"
            and _is_placeholder_secret(self.llm_api_key)
        ):
            unsafe_fields.append("LLM_API_KEY")

        if unsafe_fields:
            names = ", ".join(unsafe_fields)
            raise ValueError(
                f"ENV={self.env}: замените placeholder/стандартные значения: {names}"
            )
        return self

    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"

    cors_allowed_origins: Annotated[list[str], NoDecode]

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v: object) -> list[str]:
        if isinstance(v, str):
            v = [origin.strip() for origin in v.split(",") if origin.strip()]
        if not isinstance(v, list):
            raise ValueError("cors_allowed_origins должен быть списком origin")
        return v

    @field_validator("cors_allowed_origins")
    @classmethod
    def _forbid_wildcard_origin(cls, v: list[str]) -> list[str]:
        if "*" in v:
            raise ValueError(
                "cors_allowed_origins не может содержать «*»: укажите явные origin фронтенда"
            )
        if not v:
            raise ValueError("cors_allowed_origins не может быть пустым")
        return v

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
    return Settings()  # type: ignore[call-arg]
