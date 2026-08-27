"""
app/models/models.py
────────────────────
ORM models for the app's own PostgreSQL database.

Tables
──────
  connected_databases  – PostgreSQL connection configs saved by the user
  chat_messages        – Every query + result, stored per database
"""

import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.db.session import Base


class ConnectedDatabase(Base):
    """Stores a user-saved PostgreSQL connection (credentials + schema info)."""

    __tablename__ = "connected_databases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(120), nullable=False)          # friendly label e.g. "Sales DB"
    host = Column(String(255), nullable=False)
    port = Column(String(10), nullable=False, default="5432")
    database_name = Column(String(120), nullable=False)
    username = Column(String(120), nullable=False)
    encrypted_password = Column(Text, nullable=False)
    schema_name = Column(String(120), nullable=False, default="public")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # one database → many chat messages
    messages = relationship("ChatMessage", back_populates="database", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<ConnectedDatabase name={self.name} host={self.host}>"


class ChatMessage(Base):
    """One turn in the chat: user question → SQL → result summary + optional chart."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_database_created_at", "database_id", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    database_id = Column(
        UUID(as_uuid=True),
        ForeignKey("connected_databases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_query = Column(Text, nullable=False)
    sql_query = Column(Text, nullable=True)       # None if the LLM refused / error
    result_summary = Column(Text, nullable=True)  # bullet-point natural language answer
    result_table = Column(JSONB, nullable=True)   # raw rows from the query
    chart_config = Column(JSONB, nullable=True)   # {chart_type, x_axis, y_axis, data}
    status = Column(String(20), nullable=False, default="success")  # success | error
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    database = relationship("ConnectedDatabase", back_populates="messages")

    def __repr__(self) -> str:
        return f"<ChatMessage id={self.id} status={self.status}>"
