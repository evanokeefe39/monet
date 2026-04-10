"""Connect to the Dockerised LangGraph server and run a topic.

docker compose up -d
uv run python client.py "AI trends in healthcare"
"""

import asyncio
import sys

from monet.client import MonetClient


async def main() -> None:
    async for event in MonetClient().run(sys.argv[1], auto_approve=True):
        print(event)


asyncio.run(main())
