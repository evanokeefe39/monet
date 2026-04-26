"""Tests for monet.artifacts._migrations and the monet db CLI."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

if TYPE_CHECKING:
    from pathlib import Path

from monet.artifacts.prebuilt._migrations import (
    apply_migrations,
    check_at_head,
    current_revision,
    head_revision,
    stamp_head,
)
from monet.cli._db import db


def _file_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def _tables(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    finally:
        conn.close()
    return {row[0] for row in rows}


def test_head_revision_is_latest() -> None:
    assert head_revision() == "0004_artifacts_thread_id"


def test_artifacts_table_has_secondary_indexes(tmp_path: Path) -> None:
    """Regression guard for DA-53 — catalogue query patterns must be
    backed by indexes after the 0002 migration lands."""
    db_path = tmp_path / "index.db"
    apply_migrations(_file_url(db_path))
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='artifacts'"
        ).fetchall()
    finally:
        conn.close()
    names = {row[0] for row in rows}
    assert {
        "ix_artifacts_run_id",
        "ix_artifacts_agent_id",
        "ix_artifacts_trace_id",
        "ix_artifacts_run_created",
    }.issubset(names)


def test_query_by_run_uses_index(tmp_path: Path) -> None:
    """EXPLAIN QUERY PLAN must confirm the run_id index is chosen."""
    db_path = tmp_path / "index.db"
    apply_migrations(_file_url(db_path))
    conn = sqlite3.connect(db_path)
    try:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM artifacts WHERE run_id = 'x'"
        ).fetchall()
    finally:
        conn.close()
    detail = " ".join(str(row) for row in plan)
    assert "ix_artifacts_run" in detail, detail


def test_apply_migrations_creates_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "index.db"
    apply_migrations(_file_url(db_path))
    tables = _tables(db_path)
    assert "artifacts" in tables
    assert "alembic_version" in tables


def test_apply_migrations_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "index.db"
    url = _file_url(db_path)
    apply_migrations(url)
    apply_migrations(url)  # Re-run — must not error.
    assert check_at_head(url) is True


def test_check_at_head_false_before_migrate(tmp_path: Path) -> None:
    db_path = tmp_path / "index.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE artifacts (artifact_id TEXT PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()
    url = _file_url(db_path)
    assert check_at_head(url) is False
    assert current_revision(url) is None


def test_stamp_head_marks_existing_db(tmp_path: Path) -> None:
    db_path = tmp_path / "index.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE artifacts (artifact_id TEXT PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()
    url = _file_url(db_path)
    stamp_head(url)
    assert check_at_head(url) is True
    assert current_revision(url) == head_revision()


async def test_sqlite_index_initialise_in_memory_uses_create_all() -> None:
    """In-memory DBs bypass alembic — tests stay fast and isolated."""
    from monet.artifacts.prebuilt._index import SQLiteIndex

    idx = SQLiteIndex("sqlite+aiosqlite:///:memory:")
    await idx.initialise()  # must not raise


async def test_sqlite_index_initialise_persistent_requires_migrations(
    tmp_path: Path,
) -> None:
    """Persistent DBs must be at head before initialise succeeds."""
    from monet.artifacts.prebuilt._index import SQLiteIndex

    db_path = tmp_path / "index.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE artifacts (artifact_id TEXT PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()

    idx = SQLiteIndex(_file_url(db_path))
    with pytest.raises(RuntimeError, match="not at alembic head"):
        await idx.initialise()


async def test_sqlite_index_initialise_persistent_passes_at_head(
    tmp_path: Path,
) -> None:
    """A migrated persistent DB initialises cleanly."""
    from monet.artifacts.prebuilt._index import SQLiteIndex

    db_path = tmp_path / "index.db"
    url = _file_url(db_path)
    apply_migrations(url)  # applied out of band, like `monet db migrate`

    idx = SQLiteIndex(url)
    await idx.initialise()  # must not raise
    assert check_at_head(url) is True


# --- CLI ---


def test_cli_migrate(tmp_path: Path) -> None:
    db_path = tmp_path / "index.db"
    url = _file_url(db_path)
    runner = CliRunner()
    result = runner.invoke(db, ["migrate", "--db-url", url])
    assert result.exit_code == 0, result.output
    assert "At head" in result.output


def test_cli_current_on_fresh_db_reports_none(tmp_path: Path) -> None:
    db_path = tmp_path / "index.db"
    sqlite3.connect(db_path).close()
    runner = CliRunner()
    result = runner.invoke(db, ["current", "--db-url", _file_url(db_path)])
    assert result.exit_code == 0, result.output
    assert "no alembic_version" in result.output


def test_cli_check_fails_when_not_migrated(tmp_path: Path) -> None:
    db_path = tmp_path / "index.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE artifacts (artifact_id TEXT PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()
    runner = CliRunner()
    result = runner.invoke(db, ["check", "--db-url", _file_url(db_path)])
    assert result.exit_code == 1
    assert "not at head" in result.output


def test_cli_stamp_then_check_passes(tmp_path: Path) -> None:
    db_path = tmp_path / "index.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE artifacts (artifact_id TEXT PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()
    url = _file_url(db_path)
    runner = CliRunner()
    stamp_result = runner.invoke(db, ["stamp", "--db-url", url])
    assert stamp_result.exit_code == 0, stamp_result.output
    check_result = runner.invoke(db, ["check", "--db-url", url])
    assert check_result.exit_code == 0, check_result.output


# --- Public symbol surface ---


def test_public_exports_stable() -> None:
    """Future plumbing changes must not break the public _migrations API."""
    from monet.artifacts.prebuilt import _migrations

    for name in (
        "apply_migrations",
        "check_at_head",
        "current_revision",
        "head_revision",
        "stamp_head",
    ):
        assert hasattr(_migrations, name), name


@pytest.mark.parametrize("bad_url", ["", "   "])
def test_apply_migrations_rejects_blank_url(bad_url: str) -> None:
    with pytest.raises(Exception):  # noqa: B017 — alembic raises its own
        apply_migrations(bad_url)


def test_apply_migrations_handles_empty_alembic_version(tmp_path: Path) -> None:
    """DB with artifacts + empty alembic_version table."""
    db_path = tmp_path / "halfmigrated.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE artifacts ("
            "artifact_id VARCHAR PRIMARY KEY, "
            "content_type VARCHAR NOT NULL, "
            "content_length INTEGER NOT NULL, "
            "summary TEXT NOT NULL, "
            "confidence FLOAT NOT NULL, "
            "completeness VARCHAR NOT NULL, "
            "sensitivity_label VARCHAR NOT NULL, "
            "agent_id VARCHAR, "
            "run_id VARCHAR, "
            "trace_id VARCHAR, "
            "tags TEXT DEFAULT '{}' NOT NULL, "
            "created_at VARCHAR NOT NULL"
            ")"
        )
        conn.execute(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
        )
        conn.commit()
    finally:
        conn.close()

    url = _file_url(db_path)
    apply_migrations(url)

    assert check_at_head(url)


def test_apply_migrations_handles_pre_alembic_db(tmp_path: Path) -> None:
    """Pre-alembic DB (artifacts present, alembic_version absent) auto-stamps."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE artifacts ("
            "artifact_id VARCHAR PRIMARY KEY, "
            "content_type VARCHAR NOT NULL, "
            "content_length INTEGER NOT NULL, "
            "summary TEXT NOT NULL, "
            "confidence FLOAT NOT NULL, "
            "completeness VARCHAR NOT NULL, "
            "sensitivity_label VARCHAR NOT NULL, "
            "agent_id VARCHAR, "
            "run_id VARCHAR, "
            "trace_id VARCHAR, "
            "tags TEXT DEFAULT '{}' NOT NULL, "
            "created_at VARCHAR NOT NULL"
            ")"
        )
        conn.commit()
    finally:
        conn.close()

    url = _file_url(db_path)
    apply_migrations(url)

    assert check_at_head(url)
    assert "alembic_version" in _tables(db_path)
    assert "artifacts" in _tables(db_path)
