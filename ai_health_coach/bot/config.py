from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import PostgresDsn, RedisDsn, field_validator
from typing import Optional, List


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Telegram ─────────────────────────────────────────────────
    bot_token: str
    webhook_host: Optional[str] = None
    webhook_path: str = "/webhook"
    webhook_secret: Optional[str] = None

    # ── OpenAI ───────────────────────────────────────────────────
    openai_api_key: str
    openai_model: str = "gpt-4o"
    openai_max_tokens: int = 1000

    # ── Postgres ─────────────────────────────────────────────────
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "healthcoach"
    postgres_user: str = "healthcoach"
    postgres_password: str = "password"

    # ── Redis ────────────────────────────────────────────────────
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    # ── App ──────────────────────────────────────────────────────
    debug: bool = False
    log_level: str = "INFO"
    context_messages_limit: int = 15
    context_ttl: int = 86400  # 24 hours

    # Telegram IDs через запятую: ADMIN_IDS=123456789,987654321
    admin_ids: list[int] = []

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, v):
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip().isdigit()]
        return v or []

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url_sync(self) -> str:
        """Для Alembic миграций (sync driver)."""
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def celery_broker_url(self) -> str:
        return self.redis_url

    @property
    def celery_result_backend(self) -> str:
        return self.redis_url


settings = Settings()
