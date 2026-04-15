"""artifact secondary indexes (DA-53).

Adds indexes supporting the catalogue query patterns — per-run lookup,
agent/trace correlation, and chronological listing within a run. Before
this revision, ``SQLiteIndex.query_by_run`` ran a full table scan on
every call.

Revision ID: 0002_artifact_indexes
Revises: 0001_baseline
Create Date: 2026-04-15
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0002_artifact_indexes"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_artifacts_run_id", "artifacts", ["run_id"])
    op.create_index("ix_artifacts_agent_id", "artifacts", ["agent_id"])
    op.create_index("ix_artifacts_trace_id", "artifacts", ["trace_id"])
    op.create_index("ix_artifacts_run_created", "artifacts", ["run_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_artifacts_run_created", table_name="artifacts")
    op.drop_index("ix_artifacts_trace_id", table_name="artifacts")
    op.drop_index("ix_artifacts_agent_id", table_name="artifacts")
    op.drop_index("ix_artifacts_run_id", table_name="artifacts")
