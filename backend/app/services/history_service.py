"""
app/services/history_service.py
─────────────────────────────────
Retrieve and delete chat history for a given database connection.
"""

from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import delete

from app.core.config import AppException
from app.core.logger import get_logger
from app.models.models import ChatMessage
from app.schemas.chat_schema import ChatResponse, ChatHistoryResponse, ChartConfig, ChartData

logger = get_logger(__name__)


def _row_to_response(row: ChatMessage) -> ChatResponse:
    """Convert an ORM ChatMessage to the Pydantic response schema."""
    chart = None
    if row.chart_config:
        cfg = row.chart_config
        data = cfg.get("data", {})
        chart = ChartConfig(
            chart_type=cfg.get("chart_type", "none"),
            x_axis=cfg.get("x_axis"),
            y_axis=cfg.get("y_axis"),
            reason=cfg.get("reason", ""),
            data=ChartData(x=data.get("x", []), y=data.get("y", [])),
        )

    return ChatResponse(
        id=row.id,
        database_id=row.database_id,
        user_query=row.user_query,
        sql_query=row.sql_query,
        result_summary=row.result_summary,
        result_table=row.result_table,
        chart_config=chart,
        status=row.status,
        error_message=row.error_message,
        created_at=row.created_at,
    )


class HistoryService:
    async def get_history(
        self,
        db: AsyncSession,
        database_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> ChatHistoryResponse:
        rows = (
            await db.execute(
                select(ChatMessage)
                .where(ChatMessage.database_id == database_id)
                .order_by(ChatMessage.created_at.asc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()

        return ChatHistoryResponse(
            total=len(rows),
            messages=[_row_to_response(r) for r in rows],
        )

    async def get_message(
        self, db: AsyncSession, message_id: UUID
    ) -> ChatResponse:
        row = (
            await db.execute(
                select(ChatMessage).where(ChatMessage.id == message_id)
            )
        ).scalar_one_or_none()

        if not row:
            raise AppException(404, "Message not found.")

        return _row_to_response(row)

    async def delete_history(
        self, db: AsyncSession, database_id: UUID
    ) -> dict:
        result = await db.execute(
            delete(ChatMessage).where(ChatMessage.database_id == database_id)
        )
        await db.commit()
        deleted = result.rowcount
        logger.info(f"Deleted {deleted} messages for database_id={database_id}")
        return {"message": f"Deleted {deleted} messages.", "database_id": str(database_id)}
