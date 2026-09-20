"""add p7 approval workflow fields

Revision ID: b4d7e9f102aa
Revises: cdb0d35109b0
Create Date: 2026-09-18 11:20:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4d7e9f102aa"
down_revision: str | None = "cdb0d35109b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("approval_tasks", sa.Column("session_id", sa.String(length=48)))
    op.add_column(
        "approval_tasks", sa.Column("checkpoint_thread_id", sa.String(length=80))
    )
    op.add_column("approval_tasks", sa.Column("decided_by", sa.String(length=40)))
    op.add_column("approval_tasks", sa.Column("decision_reason", sa.String(length=500)))
    op.add_column("approval_tasks", sa.Column("expires_at", sa.DateTime(timezone=True)))
    op.add_column("approval_tasks", sa.Column("resumed_at", sa.DateTime(timezone=True)))
    op.add_column("approval_tasks", sa.Column("resume_result", sa.JSON()))
    op.add_column("approval_tasks", sa.Column("updated_at", sa.DateTime(timezone=True)))
    op.create_index("ix_approval_tasks_session_id", "approval_tasks", ["session_id"])
    op.create_index("ix_approval_tasks_expires_at", "approval_tasks", ["expires_at"])
    op.create_unique_constraint(
        "uq_approval_checkpoint_thread", "approval_tasks", ["checkpoint_thread_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_approval_checkpoint_thread", "approval_tasks", type_="unique")
    op.drop_index("ix_approval_tasks_expires_at", table_name="approval_tasks")
    op.drop_index("ix_approval_tasks_session_id", table_name="approval_tasks")
    op.drop_column("approval_tasks", "updated_at")
    op.drop_column("approval_tasks", "resume_result")
    op.drop_column("approval_tasks", "resumed_at")
    op.drop_column("approval_tasks", "expires_at")
    op.drop_column("approval_tasks", "decision_reason")
    op.drop_column("approval_tasks", "decided_by")
    op.drop_column("approval_tasks", "checkpoint_thread_id")
    op.drop_column("approval_tasks", "session_id")
