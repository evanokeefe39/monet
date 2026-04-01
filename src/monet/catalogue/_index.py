"""SQLite metadata index via SQLAlchemy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy import Column, Float, Integer, String, Table, create_engine
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from ._metadata import ArtifactMetadata

_metadata_obj = sa.MetaData()

artifacts_table = Table(
    "artifacts",
    _metadata_obj,
    Column("artifact_id", String, primary_key=True),
    Column("content_type", String, nullable=False),
    Column("content_length", Integer, nullable=False),
    Column("content_hash", String, nullable=False),
    Column("summary", String, default=""),
    Column("created_by", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("trace_id", String, default=""),
    Column("run_id", String, default=""),
    Column("invocation_command", String, default=""),
    Column("confidence", Float, default=0.0),
    Column("completeness", String, default="complete"),
    Column("sensitivity_label", String, default="internal"),
    Column("data_residency", String, default="local"),
    Column("pii_flag", Integer, default=0),  # SQLite bool
)


class SQLiteIndex:
    """SQLite-backed metadata index."""

    def __init__(self, db_url: str = "sqlite:///catalogue.db") -> None:
        self._engine = create_engine(db_url)
        _metadata_obj.create_all(self._engine)

    def insert(self, metadata: ArtifactMetadata) -> None:
        """Insert artifact metadata into the index."""
        with Session(self._engine) as session:
            session.execute(
                artifacts_table.insert().values(
                    artifact_id=metadata.artifact_id,
                    content_type=metadata.content_type,
                    content_length=metadata.content_length,
                    content_hash=metadata.content_hash,
                    summary=metadata.summary,
                    created_by=metadata.created_by,
                    created_at=metadata.created_at,
                    trace_id=metadata.trace_id,
                    run_id=metadata.run_id,
                    invocation_command=metadata.invocation_command,
                    confidence=metadata.confidence,
                    completeness=metadata.completeness,
                    sensitivity_label=metadata.sensitivity_label,
                    data_residency=metadata.data_residency,
                    pii_flag=int(metadata.pii_flag),
                )
            )
            session.commit()

    def query_by_id(self, artifact_id: str) -> dict[str, Any] | None:
        """Query metadata by artifact_id."""
        with Session(self._engine) as session:
            result = session.execute(
                artifacts_table.select().where(
                    artifacts_table.c.artifact_id == artifact_id
                )
            )
            row = result.mappings().first()
            return dict(row) if row else None

    def query_by_trace(self, trace_id: str) -> list[dict[str, Any]]:
        """Query all artifacts for a given trace."""
        with Session(self._engine) as session:
            result = session.execute(
                artifacts_table.select().where(artifacts_table.c.trace_id == trace_id)
            )
            return [dict(row) for row in result.mappings()]

    def query_by_run(self, run_id: str) -> list[dict[str, Any]]:
        """Query all artifacts for a given run."""
        with Session(self._engine) as session:
            result = session.execute(
                artifacts_table.select().where(artifacts_table.c.run_id == run_id)
            )
            return [dict(row) for row in result.mappings()]
