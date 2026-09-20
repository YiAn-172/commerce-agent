"""add p7 knowledge API persistence

Revision ID: d2a6f4108c3b
Revises: b4d7e9f102aa
Create Date: 2026-09-18 11:25:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2a6f4108c3b"
down_revision: str | None = "b4d7e9f102aa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    document_columns = {
        item["name"] for item in inspector.get_columns("knowledge_documents")
    }
    if "content_text" not in document_columns:
        op.add_column("knowledge_documents", sa.Column("content_text", sa.Text()))
    if "created_by" not in document_columns:
        op.add_column("knowledge_documents", sa.Column("created_by", sa.String(length=40)))
    op.execute(
        "UPDATE knowledge_documents "
        "SET content_text = '', created_by = 'system_migration' "
        "WHERE content_text IS NULL OR created_by IS NULL"
    )
    op.alter_column(
        "knowledge_documents",
        "content_text",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.alter_column(
        "knowledge_documents",
        "created_by",
        existing_type=sa.String(length=40),
        nullable=False,
    )
    inspector = sa.inspect(op.get_bind())
    if "knowledge_reindex_jobs" not in inspector.get_table_names():
        op.create_table(
            "knowledge_reindex_jobs",
            sa.Column("id", sa.String(length=48), nullable=False),
            sa.Column("requested_by", sa.String(length=40), nullable=False),
            sa.Column("document_ids", sa.JSON(), nullable=False),
            sa.Column("target_version", sa.String(length=40), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("error_summary", sa.String(length=500)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True)),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_knowledge_reindex_jobs_status",
            "knowledge_reindex_jobs",
            ["status"],
        )


def downgrade() -> None:
    op.drop_index("ix_knowledge_reindex_jobs_status", table_name="knowledge_reindex_jobs")
    op.drop_table("knowledge_reindex_jobs")
    op.drop_column("knowledge_documents", "created_by")
    op.drop_column("knowledge_documents", "content_text")
