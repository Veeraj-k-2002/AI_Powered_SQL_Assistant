"""
app/routes/history_routes.py
─────────────────────────────
Endpoints for reading and deleting chat history.
"""

from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.chat_schema import ChatResponse, ChatHistoryResponse
from app.services.history_service import HistoryService
from app.core.config import AppException
from app.core.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/history", tags=["History"])
_svc = HistoryService()


@router.get(
    "/database/{database_id}",
    response_model=ChatHistoryResponse,
    summary="Get all messages for a database",
)
async def get_history(
    database_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await _svc.get_history(db, database_id, limit, offset)
    except AppException as e:
        raise HTTPException(e.status_code, e.detail)


@router.get("/message/{message_id}", response_model=ChatResponse)
async def get_message(message_id: UUID, db: AsyncSession = Depends(get_db)):
    try:
        return await _svc.get_message(db, message_id)
    except AppException as e:
        raise HTTPException(e.status_code, e.detail)


@router.delete("/database/{database_id}", summary="Clear all messages for a database")
async def delete_history(database_id: UUID, db: AsyncSession = Depends(get_db)):
    try:
        return await _svc.delete_history(db, database_id)
    except AppException as e:
        raise HTTPException(e.status_code, e.detail)
