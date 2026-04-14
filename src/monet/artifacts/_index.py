"""SQLite metadata index via SQLAlchemy async with aiosqlite."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from sqlalchemy import Float, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

if TYPE_CHECKING:
    from monet.artifacts._metadata import ArtifactMetadata


class Base(DeclarativeBase):
    pass


class ArtifactRecord(Base):
    """ORM model for the artifacts table. Distinct from ArtifactMetadata TypedDict."""

    __tablename__ = "artifacts"
    artifact_id: Mapped[str] = mapped_column(String, primary_key=True)
    content_type: Mapped[str] = mapped_column(String)
    content_length: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    completeness: Mapped[str] = mapped_column(String)
    sensitivity_label: Mapped[str] = mapped_column(String)
    agent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String, nullable=True)
    tags: Mapped[str] = mapped_column(Text, default="{}")  # JSON string
    created_at: Mapped[str] = mapped_column(String)


class SQLiteIndex:
    """SQLite-backed metadata index using async SQLAlchemy.

    DB URL must use sqlite+aiosqlite:// scheme.
    """

    def __init__(self, db_url: str = "sqlite+aiosqlite:///.artifacts/index.db") -> None:
        self._engine = create_async_engine(db_url)

    async def initialise(self) -> None:
        """Create tables. Call once at startup."""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def put(self, metadata: ArtifactMetadata) -> None:
        """Insert artifact metadata into the index.

        Receives an ArtifactMetadata TypedDict and maps it to an ArtifactRecord.
        """
        import json

        record_data = dict(metadata)
        # Convert tags dict to JSON string for storage
        record_data["tags"] = json.dumps(record_data.get("tags", {}))
        async with AsyncSession(self._engine) as session:
            session.add(ArtifactRecord(**record_data))
            await session.commit()

    async def get(self, artifact_id: str) -> ArtifactMetadata | None:
        """Query metadata by artifact_id."""
        import json

        async with AsyncSession(self._engine) as session:
            result = await session.get(ArtifactRecord, artifact_id)
            if result is None:
                return None
            row_dict = {c.key: getattr(result, c.key) for c in result.__table__.columns}
            # Convert tags JSON string back to dict
            row_dict["tags"] = json.loads(row_dict.get("tags", "{}"))
            return cast("ArtifactMetadata", row_dict)

    async def query_by_run(self, run_id: str) -> list[ArtifactMetadata]:
        """Query all artifacts for a given run."""
        import json

        async with AsyncSession(self._engine) as session:
            stmt = select(ArtifactRecord).where(ArtifactRecord.run_id == run_id)
            rows = await session.execute(stmt)
            results = []
            for r in rows.scalars():
                row_dict = {c.key: getattr(r, c.key) for c in r.__table__.columns}
                row_dict["tags"] = json.loads(row_dict.get("tags", "{}"))
                results.append(cast("ArtifactMetadata", row_dict))
            return results
