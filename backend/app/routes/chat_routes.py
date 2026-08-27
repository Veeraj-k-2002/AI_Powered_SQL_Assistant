"""
app/routes/chat_routes.py
──────────────────────────
POST /chat/ask  – main chat endpoint
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.chat_schema import ChatRequest, ChatResponse
from app.services.sql_chat_service import SqlChatService
from app.services.history_service import HistoryService, _row_to_response
from app.core.config import AppException
from app.core.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/chat", tags=["Chat"])


@router.post("/ask", response_model=ChatResponse, summary="Ask a natural-language question")
async def ask(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Convert a natural-language question to SQL, run it against the connected
    PostgreSQL database, and return the result with an optional chart config.
    """
    try:
        service = SqlChatService(db)
        message = await service.chat(request.database_id, request.user_query)
        return _row_to_response(message)
    except AppException as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except Exception as exc:
        logger.exception("Unexpected chat request failure (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="An internal error occurred while processing the request.")
