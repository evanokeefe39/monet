from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from monet.core._jsonpath import extract

from ._errors import AdapterError
from ._types import TaskRequest, TaskResponse

if TYPE_CHECKING:
    from ._config import AdapterConfig


def _resolve_body(template: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Walk template; string values starting with '$.' are JSONPath expressions."""
    result: dict[str, Any] = {}
    for key, val in template.items():
        if isinstance(val, str) and val.startswith("$."):
            result[key] = extract(incoming, val)
        elif isinstance(val, dict):
            result[key] = _resolve_body(val, incoming)
        else:
            result[key] = val
    return result


class HTTPProxy:
    def __init__(self, config: AdapterConfig) -> None:
        self._config = config

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._config.auth:
            headers["Authorization"] = self._config.auth
        headers.update(self._config.headers)
        return headers

    async def handle_task(self, request: TaskRequest) -> TaskResponse:
        incoming: dict[str, Any] = {
            "task_id": request.task_id,
            "payload": request.payload,
        }
        body = _resolve_body(self._config.request.body, incoming)
        params = self._config.request.params

        try:
            async with httpx.AsyncClient(timeout=float(self._config.timeout)) as client:
                resp = await client.request(
                    self._config.request.method,
                    self._config.url,
                    json=body,
                    params=params,
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()

            output = str(extract(data, self._config.response.output))
            artifacts = {
                k: str(extract(data, path))
                for k, path in self._config.response.artifacts.items()
            }
            return TaskResponse(output=output, artifacts=artifacts)
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
