"""
app/db/session.py
─────────────────
Creates the async SQLAlchemy engine + session factory for the app's own
PostgreSQL database (stores database configs and chat history).
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings

# ── Engine ──────────────────────────────────────────────────────────────────
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=(settings.APP_ENV == "development"),  # log SQL in dev
    pool_pre_ping=True,                         # drop stale connections
    pool_size=10,
    max_overflow=20,
)

# ── Session factory ─────────────────────────────────────────────────────────
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ── Base model ───────────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


# ── FastAPI dependency ───────────────────────────────────────────────────────
async def get_db():
    """Yield a database session and close it after the request."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ── Schema initialisation ────────────────────────────────────────────────────
async def init_db():
    """Load model metadata; schema changes are applied only by Alembic."""
    from app.models import models  # noqa: F401
