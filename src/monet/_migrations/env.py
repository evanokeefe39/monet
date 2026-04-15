"""Alembic environment for the monet artifact index.

Always runs against a synchronous driver. ``apply_migrations`` in
:mod:`monet.artifacts._migrations` converts any ``+aiosqlite`` /
``+asyncpg`` URL to its sync counterpart before passing it here, so this
env never needs to spin up its own asyncio loop. Running sync avoids the
nested-event-loop hazard when migrations are invoked from inside an
already-running coroutine (e.g. pytest-asyncio tests).

URL resolution order:

1. ``config.get_main_option("sqlalchemy.url")`` — set by the caller.
2. ``MONET_ARTIFACTS_DB_URL`` environment variable.
3. Fallback to ``sqlite:///.artifacts/index.db``.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, engine_from_config, pool

from monet.artifacts._index import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_url() -> str:
    configured: str | None = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured
    override = os.environ.get("MONET_ARTIFACTS_DB_URL")
    if override:
        return override
    return "sqlite:///.artifacts/index.db"


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _resolve_url()
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        _do_run_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
