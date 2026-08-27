"""
app/services/database_service.py
──────────────────────────────────
CRUD for user-saved PostgreSQL connections.
Also provides a live connection test.
"""

from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.future import select
from sqlalchemy import text, inspect, URL

from app.core.config import AppException
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.logger import get_logger
from app.models.models import ConnectedDatabase
from app.schemas.database_schema import (
    DatabaseCreate,
    DatabaseUpdate,
    DatabaseResponse,
    DatabaseDeleteResponse,
    ConnectionTestResponse,
)

logger = get_logger(__name__)


class DatabaseService:
    # ── Create ───────────────────────────────────────────────────────────────

    async def create(
        self, db: AsyncSession, data: DatabaseCreate
    ) -> DatabaseResponse:
        record = ConnectedDatabase(
            name=data.name,
            host=data.host,
            port=data.port,
            database_name=data.database_name,
            username=data.username,
            encrypted_password=encrypt_secret(data.password),
            schema_name=data.schema_name,
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        logger.info(f"Created database connection '{record.name}' id={record.id}")
        return DatabaseResponse.model_validate(record)

    # ── Read all ─────────────────────────────────────────────────────────────

    async def list_all(self, db: AsyncSession) -> list[DatabaseResponse]:
        rows = (await db.execute(select(ConnectedDatabase))).scalars().all()
        return [DatabaseResponse.model_validate(r) for r in rows]

    # ── Read one ─────────────────────────────────────────────────────────────

    async def get(self, db: AsyncSession, database_id: UUID) -> DatabaseResponse:
        record = await self._get_or_404(db, database_id)
        return DatabaseResponse.model_validate(record)

    # ── Update ───────────────────────────────────────────────────────────────

    async def update(
        self, db: AsyncSession, database_id: UUID, data: DatabaseUpdate
    ) -> DatabaseResponse:
        record = await self._get_or_404(db, database_id)

        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(record, field, value)

        await db.commit()
        await db.refresh(record)
        return DatabaseResponse.model_validate(record)

    # ── Delete ───────────────────────────────────────────────────────────────

    async def delete(
        self, db: AsyncSession, database_id: UUID
    ) -> DatabaseDeleteResponse:
        record = await self._get_or_404(db, database_id)
        await db.delete(record)
        await db.commit()
        logger.info(f"Deleted database connection id={database_id}")
        return DatabaseDeleteResponse(
            message="Database connection deleted.",
            deleted_id=str(database_id),
        )

    # ── Test connection ───────────────────────────────────────────────────────

    async def test_connection(
        self, db: AsyncSession, database_id: UUID
    ) -> ConnectionTestResponse:
        record = await self._get_or_404(db, database_id)
        try:
            uri = URL.create(
                "postgresql+asyncpg",
                username=record.username,
                password=decrypt_secret(record.encrypted_password),
                host=record.host,
                port=int(record.port),
                database=record.database_name,
            )
        except ValueError as exc:
            logger.error("Could not decrypt credentials for database id=%s", database_id)
            raise AppException(500, "Saved database credentials are unavailable. Update the connection.") from exc
        engine = create_async_engine(uri)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))

                def _tables(sync_conn):
                    return inspect(sync_conn).get_table_names(schema=record.schema_name)

                tables = await conn.run_sync(_tables)

            return ConnectionTestResponse(
                success=True,
                message=f"Connected successfully to '{record.database_name}'.",
                tables_found=tables,
            )
        except Exception as exc:
            logger.warning("Connection test failed for database id=%s (%s)", database_id, type(exc).__name__)
            return ConnectionTestResponse(
                success=False,
                message="Could not connect with the saved credentials. Check the connection details and try again.",
            )
        finally:
            await engine.dispose()

    # ── Internal helper ───────────────────────────────────────────────────────

    async def _get_or_404(
        self, db: AsyncSession, database_id: UUID
    ) -> ConnectedDatabase:
        record = (
            await db.execute(
                select(ConnectedDatabase).where(ConnectedDatabase.id == database_id)
            )
        ).scalar_one_or_none()

        if not record:
            raise AppException(404, f"Database connection '{database_id}' not found.")

        return record
