"""artifacts.key column.

Stores the named key used to identify an artifact within a run. Enables
artifact lookup by semantic key rather than opaque artifact_id. Nullable
to preserve compatibility with records written before this migration.

Revision ID: 0005_artifact_key
Revises: 0004_artifacts_thread_id
Create Date: 2026-04-30
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0005_artifact_key"
down_revision: str | None = "0004_artifacts_thread_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("artifacts", sa.Column("key", sa.String(), nullable=True))
    op.create_index("ix_artifacts_key", "artifacts", ["key"])


def downgrade() -> None:
    op.drop_index("ix_artifacts_key", table_name="artifacts")
    op.drop_column("artifacts", "key")
