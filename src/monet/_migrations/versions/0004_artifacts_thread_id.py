"""artifacts.thread_id column + index.

Adds thread_id provenance alongside run_id / trace_id / agent_id so the
chat TUI (and downstream meta-agents) can count "artifacts written for
this thread" without traversing runs. Populated automatically by
``ArtifactService.write`` from the run context when present.

Revision ID: 0004_artifacts_thread_id
Revises: 0003_artifact_query_recent_indexes
Create Date: 2026-04-17
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0004_artifacts_thread_id"
down_revision: str | None = "0003_artifact_query_recent_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("artifacts", sa.Column("thread_id", sa.String(), nullable=True))
    op.create_index("ix_artifacts_thread_id", "artifacts", ["thread_id"])


def downgrade() -> None:
    op.drop_index("ix_artifacts_thread_id", table_name="artifacts")
    op.drop_column("artifacts", "thread_id")
