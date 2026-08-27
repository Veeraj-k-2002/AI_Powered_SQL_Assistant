"""Create the encrypted connection and chat-history schema.

Revision ID: 20260823_01
Revises: None
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from app.core.crypto import encrypt_secret


revision = "20260823_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("connected_databases"):
        op.create_table(
            "connected_databases",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("host", sa.String(length=255), nullable=False),
            sa.Column("port", sa.String(length=10), nullable=False),
            sa.Column("database_name", sa.String(length=120), nullable=False),
            sa.Column("username", sa.String(length=120), nullable=False),
            sa.Column("encrypted_password", sa.Text(), nullable=False),
            sa.Column("schema_name", sa.String(length=120), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    else:
        column_names = {column["name"] for column in inspector.get_columns("connected_databases")}
        if "password" in column_names:
            # Existing installations used this column for plaintext. Rename then
            # encrypt every value in the same migration before the app starts.
            op.alter_column("connected_databases", "password", new_column_name="encrypted_password")
            rows = bind.execute(sa.text("SELECT id, encrypted_password FROM connected_databases")).mappings()
            for row in rows:
                bind.execute(
                    sa.text("UPDATE connected_databases SET encrypted_password = :secret WHERE id = :id"),
                    {"id": row["id"], "secret": encrypt_secret(row["encrypted_password"])} ,
                )

    inspector = sa.inspect(bind)
    if not inspector.has_table("chat_messages"):
        op.create_table(
            "chat_messages",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("database_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_query", sa.Text(), nullable=False),
            sa.Column("sql_query", sa.Text(), nullable=True),
            sa.Column("result_summary", sa.Text(), nullable=True),
            sa.Column("result_table", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("chart_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["database_id"], ["connected_databases.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("chat_messages")}
    if "ix_chat_messages_database_id" not in indexes:
        op.create_index("ix_chat_messages_database_id", "chat_messages", ["database_id"])
    if "ix_chat_messages_database_created_at" not in indexes:
        op.create_index("ix_chat_messages_database_created_at", "chat_messages", ["database_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_chat_messages_database_created_at", table_name="chat_messages")
    op.drop_index("ix_chat_messages_database_id", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_table("connected_databases")
