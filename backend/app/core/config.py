"""
app/core/config.py
──────────────────
Central configuration loaded from environment variables via Pydantic Settings.
"""

import base64
import hashlib

from pydantic import field_validator
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    APP_NAME: str = "AI-Powered SQL Assistant"
    APP_ENV: str = "development"
    SECRET_KEY: str = "changeme"
    # Use a separately managed Fernet key in production. When omitted in local
    # development, a deterministic key is derived from SECRET_KEY for convenience.
    DATABASE_ENCRYPTION_KEY: str = ""
    BACKEND_URL: str = "http://localhost:8000"

    # Query safety limits. These are deliberately bounded server-side.
    MAX_QUERY_ROWS: int = 500
    QUERY_TIMEOUT_MS: int = 15_000
    QUERY_CACHE_TTL_SECONDS: int = 60
    QUERY_CACHE_MAX_ENTRIES: int = 200

    # Groq (free LLM API)
    GROQ_API_KEY: str

    # App's own PostgreSQL (stores configs + history)
    DATABASE_URL: str  # postgresql+asyncpg://...

    # Optional Redis
    # REDIS_URL: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"

    @field_validator("MAX_QUERY_ROWS")
    @classmethod
    def validate_max_rows(cls, value: int) -> int:
        if not 1 <= value <= 10_000:
            raise ValueError("MAX_QUERY_ROWS must be between 1 and 10000")
        return value

    @field_validator("QUERY_TIMEOUT_MS")
    @classmethod
    def validate_timeout(cls, value: int) -> int:
        if not 100 <= value <= 120_000:
            raise ValueError("QUERY_TIMEOUT_MS must be between 100 and 120000")
        return value

    def fernet_key(self) -> bytes:
        """Return the configured key, or a development-only derived Fernet key."""
        if self.DATABASE_ENCRYPTION_KEY:
            return self.DATABASE_ENCRYPTION_KEY.encode()
        if self.APP_ENV.lower() == "production":
            raise ValueError("DATABASE_ENCRYPTION_KEY is required in production")
        digest = hashlib.sha256(self.SECRET_KEY.encode()).digest()
        return base64.urlsafe_b64encode(digest)


@lru_cache()          # singleton — read .env once
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


# ── Custom exception ────────────────────────────────────────────────────────
class AppException(Exception):
    """Raised by services with a meaningful HTTP status code and message."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)
