from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, urlunparse

import httpx

from ._errors import AdapterError
from ._types import TaskRequest, TaskResponse

if TYPE_CHECKING:
    from ._config import AdapterConfig


def _normalize_url(url: str) -> str:
    """Append /v1/chat/completions when url has no path or ends in /v1."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if not path:
        return urlunparse(parsed._replace(path="/v1/chat/completions"))
    if path == "/v1":
        return urlunparse(parsed._replace(path="/v1/chat/completions"))
    return url


class OpenAIProxy:
    def __init__(self, config: AdapterConfig) -> None:
        self._config = config
        self._url = _normalize_url(config.url)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        auth = self._config.auth
        if not auth:
            key = os.environ.get("OPENAI_API_KEY")
            if key:
                auth = f"Bearer {key}"
        if auth:
            headers["Authorization"] = auth
        headers.update(self._config.headers)
        return headers

    async def handle_task(self, request: TaskRequest) -> TaskResponse:
        body: dict[str, Any] = {"messages": [{"role": "user", "content": request.task}]}
        if self._config.model:
            body["model"] = self._config.model

        try:
            async with httpx.AsyncClient(timeout=float(self._config.timeout)) as client:
                resp = await client.post(self._url, json=body, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
            output: str = data["choices"][0]["message"]["content"]
            return TaskResponse(output=output)
        except httpx.HTTPStatusError as exc:
            raise AdapterError(
                f"Upstream returned {exc.response.status_code}", "UPSTREAM_ERROR"
            ) from exc
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(str(exc), "UPSTREAM_ERROR") from exc

    async def close(self) -> None:
        pass
