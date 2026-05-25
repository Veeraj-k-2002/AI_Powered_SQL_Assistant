"""
app/schemas/chat_schema.py
──────────────────────────
Pydantic models for the chat endpoint and history responses.
"""

from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime
from typing import Optional, Any


# ── Chat request ─────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    database_id: UUID
    user_query: str = Field(..., min_length=3, example="Show me total sales by region")


# ── Chart payload embedded in ChatResponse ───────────────────────────────────

class ChartData(BaseModel):
    x: list = []
    y: list = []


class ChartConfig(BaseModel):
    chart_type: str = "none"   # bar | line | scatter | pie | none
    x_axis: Optional[str] = None
    y_axis: Optional[str] = None
    reason: str = ""
    data: ChartData = ChartData()


# ── Chat response ─────────────────────────────────────────────────────────────

class ChatResponse(BaseModel):
    id: UUID
    database_id: UUID
    user_query: str
    sql_query: Optional[str] = None
    result_summary: Optional[str] = None
    result_table: Optional[list[dict[str, Any]]] = None
    chart_config: Optional[ChartConfig] = None
    status: str
    error_message: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ── History list response ─────────────────────────────────────────────────────

class ChatHistoryResponse(BaseModel):
    total: int
    messages: list[ChatResponse]
