"""
app/core/config.py
──────────────────
Central configuration loaded from environment variables via Pydantic Settings.
"""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    APP_NAME: str = "AI-Powered SQL Assistant"
    APP_ENV: str = "development"
    SECRET_KEY: str = "changeme"
    BACKEND_URL: str = "http://localhost:8000"

    # Groq (free LLM API)
    GROQ_API_KEY: str

    # App's own PostgreSQL (stores configs + history)
    DATABASE_URL: str  # postgresql+asyncpg://...

    # Optional Redis
    # REDIS_URL: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"


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
