"""Run the full pipeline in-process — no server, one terminal.

uv run python run.py "AI trends in healthcare"
"""

import asyncio
import sys

from monet import run


async def main() -> None:
    async for event in run(sys.argv[1]):
        print(event)


asyncio.run(main())
