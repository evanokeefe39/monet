"""artifact indexes supporting query_recent filters (DA-20, DA-53).

``SQLiteIndex.query_recent`` filters on ``agent_id`` and ``created_at``
and sorts by ``created_at`` descending. The existing ``ix_artifacts_agent_id``
covers equality on ``agent_id`` but forces a filesort when paired with
``ORDER BY created_at DESC LIMIT``. This revision adds:

- ``ix_artifacts_agent_created`` — composite ``(agent_id, created_at)``
  covering "recent artifacts for this agent" (hot path for the
  ``data_analyst(score_agents)`` telemetry pipeline).
- ``ix_artifacts_created_at`` — plain ``created_at`` covering the
  unfiltered "most recent artifacts overall" path used by ad-hoc
  operator queries.

Tag filtering still goes through an unindexed ``LIKE`` on the JSON
string in the ``tags`` column — acceptable while tag vocabularies stay
small. When a user surfaces a workload that stresses tag lookup, the
next migration should normalise tags into a child table or use
SQLite's ``json_extract``-backed expression index.

Revision ID: 0003_artifact_query_recent_indexes
Revises: 0002_artifact_indexes
Create Date: 2026-04-17
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0003_artifact_query_recent_indexes"
down_revision: str | None = "0002_artifact_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_artifacts_agent_created",
        "artifacts",
        ["agent_id", "created_at"],
    )
    op.create_index("ix_artifacts_created_at", "artifacts", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_artifacts_created_at", table_name="artifacts")
    op.drop_index("ix_artifacts_agent_created", table_name="artifacts")
