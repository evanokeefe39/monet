"""Programmatic alembic runner for the artifact index.

Shared entry point for both :meth:`SQLiteIndex.initialise` and the
``monet db`` CLI. Always uses the package-shipped migrations under
``monet._migrations`` so the correct revision set travels with the
installed wheel.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine


def _alembic_config(db_url: str | None = None) -> Config:
    """Build an alembic Config pointing at the package-shipped migrations."""
    script_location = Path(str(files("monet._migrations")))
    cfg = Config()
    cfg.set_main_option("script_location", str(script_location))
    cfg.set_main_option("prepend_sys_path", ".")
    cfg.set_main_option("path_separator", "os")
    cfg.set_main_option(
        "file_template",
        "%%(year)d_%%(month).2d_%%(day).2d_%%(rev)s_%%(slug)s",
    )
    if db_url is not None:
        cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _sync_url(db_url: str) -> str:
    """Return a sync SQLAlchemy URL for the given async URL.

    Alembic-level checks use a synchronous connection; the async
    driver suffix is stripped for that purpose only.
    """
    return db_url.replace("+aiosqlite", "").replace("+asyncpg", "")


def apply_migrations(db_url: str) -> None:
    """Upgrade the target database to alembic head. Idempotent.

    Always dispatches with a sync driver URL to avoid nested asyncio
    loops when invoked from within an async context.
    """
    cfg = _alembic_config(_sync_url(db_url))
    command.upgrade(cfg, "head")


def current_revision(db_url: str) -> str | None:
    """Return the current alembic revision applied to the DB, or None."""
    engine = create_engine(_sync_url(db_url))
    try:
        with engine.connect() as connection:
            mc = MigrationContext.configure(connection)
            rev: str | None = mc.get_current_revision()
            return rev
    finally:
        engine.dispose()


def head_revision() -> str | None:
    """Return the head revision declared by the shipped migrations."""
    cfg = _alembic_config()
    script = ScriptDirectory.from_config(cfg)
    head: str | None = script.get_current_head()
    return head


def check_at_head(db_url: str) -> bool:
    """True if the DB is at the latest migration head."""
    return current_revision(db_url) == head_revision()


def stamp_head(db_url: str) -> None:
    """Mark the DB as being at head without running migrations.

    Use this when adopting alembic on a pre-existing database that was
    created by legacy ``Base.metadata.create_all``.
    """
    cfg = _alembic_config(_sync_url(db_url))
    command.stamp(cfg, "head")
