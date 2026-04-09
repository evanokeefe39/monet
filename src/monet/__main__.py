"""CLI entry point for monet — wires the catalogue and runs the message.

Usage:
    python -m monet "Write a post about AI"

Catalogue is initialised before configure_catalogue() because SQLiteIndex
creates tables lazily; without initialise() the first write would fail.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path


async def _main(message: str) -> None:
    from monet.catalogue import (
        CatalogueService,
        FilesystemStorage,
        SQLiteIndex,
        configure_catalogue,
    )
    from monet.orchestration import run

    catalogue_dir = Path(os.environ.get("MONET_CATALOGUE_DIR", ".catalogue"))
    catalogue_dir.mkdir(parents=True, exist_ok=True)
    catalogue = CatalogueService(
        storage=FilesystemStorage(catalogue_dir / "artifacts"),
        index=SQLiteIndex(f"sqlite+aiosqlite:///{catalogue_dir}/index.db"),
    )
    await catalogue.initialise()
    configure_catalogue(catalogue)

    import monet.agents  # noqa: F401 — registers reference agents
    from monet._queue_memory import InMemoryTaskQueue
    from monet._queue_worker import run_worker
    from monet._registry import default_registry
    from monet.orchestration._invoke import configure_queue

    queue = InMemoryTaskQueue()
    configure_queue(queue)
    worker_task = asyncio.create_task(run_worker(queue, default_registry))

    import contextlib

    try:
        result = await run(message)
        print(json.dumps({"phase": result.get("phase")}, indent=2))
    finally:
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task


def main() -> None:
    message = " ".join(sys.argv[1:]).strip()
    if not message:
        message = input("Enter message: ").strip()
    if not message:
        print("No message provided.")
        return
    asyncio.run(_main(message))


if __name__ == "__main__":
    main()
