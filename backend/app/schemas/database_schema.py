"""
app/schemas/database_schema.py
───────────────────────────────
Pydantic request / response models for connected databases.
"""

from pydantic import BaseModel, Field, field_validator
from uuid import UUID
from datetime import datetime
from typing import Optional


class DatabaseCreate(BaseModel):
    """Body sent by the frontend when saving a new database connection."""

    name: str = Field(..., min_length=1, max_length=120, example="Sales DB")
    host: str = Field(..., example="db.example.com")
    port: str = Field(default="5432", example="5432")
    database_name: str = Field(..., example="sales")
    username: str = Field(..., example="readonly_user")
    password: str = Field(..., example="supersecret")
    schema_name: str = Field(default="public", example="public")

    @field_validator("port")
    @classmethod
    def validate_port(cls, value: str) -> str:
        if not value.isdigit() or not 1 <= int(value) <= 65535:
            raise ValueError("port must be between 1 and 65535")
        return value


class DatabaseUpdate(BaseModel):
    name: Optional[str] = None
    schema_name: Optional[str] = None


class DatabaseResponse(BaseModel):
    id: UUID
    name: str
    host: str
    port: str
    database_name: str
    username: str
    schema_name: str
    created_at: datetime

    class Config:
        from_attributes = True


class DatabaseDeleteResponse(BaseModel):
    message: str
    deleted_id: str


class ConnectionTestResponse(BaseModel):
    success: bool
    message: str
    tables_found: list[str] = []
