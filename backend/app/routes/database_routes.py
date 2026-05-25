"""
app/routes/database_routes.py
───────────────────────────────
CRUD endpoints for saved PostgreSQL connections.
"""

from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.database_schema import (
    DatabaseCreate,
    DatabaseUpdate,
    DatabaseResponse,
    DatabaseDeleteResponse,
    ConnectionTestResponse,
)
from app.services.database_service import DatabaseService
from app.core.config import AppException
from app.core.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/databases", tags=["Databases"])
_svc = DatabaseService()


@router.get("/", response_model=list[DatabaseResponse], summary="List all saved connections")
async def list_databases(db: AsyncSession = Depends(get_db)):
    try:
        return await _svc.list_all(db)
    except AppException as e:
        raise HTTPException(e.status_code, e.detail)


@router.post("/", response_model=DatabaseResponse, status_code=201, summary="Add a new connection")
async def create_database(data: DatabaseCreate, db: AsyncSession = Depends(get_db)):
    try:
        return await _svc.create(db, data)
    except AppException as e:
        raise HTTPException(e.status_code, e.detail)


@router.get("/{database_id}", response_model=DatabaseResponse)
async def get_database(database_id: UUID, db: AsyncSession = Depends(get_db)):
    try:
        return await _svc.get(db, database_id)
    except AppException as e:
        raise HTTPException(e.status_code, e.detail)


@router.put("/{database_id}", response_model=DatabaseResponse, summary="Update name or schema")
async def update_database(
    database_id: UUID, data: DatabaseUpdate, db: AsyncSession = Depends(get_db)
):
    try:
        return await _svc.update(db, database_id, data)
    except AppException as e:
        raise HTTPException(e.status_code, e.detail)


@router.delete("/{database_id}", response_model=DatabaseDeleteResponse)
async def delete_database(database_id: UUID, db: AsyncSession = Depends(get_db)):
    try:
        return await _svc.delete(db, database_id)
    except AppException as e:
        raise HTTPException(e.status_code, e.detail)


@router.get("/{database_id}/test", response_model=ConnectionTestResponse, summary="Test a connection")
async def test_connection(database_id: UUID, db: AsyncSession = Depends(get_db)):
    try:
        return await _svc.test_connection(db, database_id)
    except AppException as e:
        raise HTTPException(e.status_code, e.detail)
