"""
app/main.py
────────────
FastAPI application factory.
Registers routers, runs DB init on startup, and adds CORS for the
Streamlit frontend running on http://localhost:8501.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logger import get_logger
from app.db.session import init_db
from app.routes import chat_routes, database_routes, history_routes

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables on startup."""
    logger.info("Starting up — initialising database tables…")
    await init_db()
    logger.info("Database ready.")
    yield
    logger.info("Shutting down.")


app = FastAPI(
    title=settings.APP_NAME,
    description="Ask natural-language questions about your PostgreSQL database.",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS ─────────────────────────────────────────────────────────────────────
# Allow the Streamlit frontend (port 8501) to call the backend (port 8000).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://127.0.0.1:8501"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(chat_routes.router,     prefix="/api/v1")
app.include_router(database_routes.router, prefix="/api/v1")
app.include_router(history_routes.router,  prefix="/api/v1")


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok", "app": settings.APP_NAME}
