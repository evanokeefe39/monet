"""Tools for the data_analyst agent.

Two tools ship here, both shaped as plain async functions so they are
trivially composable — and both also decorated as LangChain ``@tool`` so
an LLM-driven agent can bind them via ``llm.bind_tools([...])``:

- ``artifact_query``: reads monet's artifact index (``run_summary``,
  ``trial_scorecard``, whatever the hook or an agent wrote). This is the
  reference pattern for exposing the artifact store to agents — mirrors
  how ``researcher`` uses Exa / Tavily as search tools. Future tools for
  Postgres, Neon, other data stores, and MCP servers follow the same
  shape.
- ``otel_query``: reads OTel span data (``gen_ai.usage.*`` tokens,
  per-call latency, retry counts) via a pluggable backend. The shipped
  dev-mode backend reads the local JSONL trace file monet writes when
  tracing is configured with a file exporter. Production backends
  (Langfuse, Datadog, Honeycomb, Jaeger, …) implement the same
  :class:`OtelQueryBackend` protocol.

Both tools are pure query surfaces — no writes. The agent reasons over
their output; the SDK does not model "what the data means".
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol

from langchain_core.tools import tool

from monet import get_artifacts

# ── artifact_query ────────────────────────────────────────────────────


@tool
async def artifact_query(
    agent_id: str | None = None,
    tag: str | None = None,
    since: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Query monet's artifact index for artifact metadata.

    Args:
        agent_id: Only return artifacts written by this agent, if set.
        tag: Only return artifacts whose tag dict contains this key, if set.
        since: ISO-8601 UTC timestamp — only artifacts created at or after.
        limit: Max rows returned, ordered newest first (default 100).

    Returns a list of artifact metadata dicts. Each row carries
    ``artifact_id``, ``agent_id``, ``run_id``, ``trace_id``, ``tags``,
    ``created_at``, plus sidecar fields like ``confidence`` and
    ``completeness``.

    Pair with ``otel_query`` to join artifact-level outcomes with
    span-level telemetry (tokens, retries, per-call latency).
    """
    rows = await get_artifacts().query_recent(
        agent_id=agent_id, tag=tag, since=since, limit=limit
    )
    # LangChain serialises tool outputs as JSON; the row dicts are already
    # JSON-safe since query_recent deserialises tags and stores created_at
    # as an ISO string.
    return list(rows)


# ── otel_query ────────────────────────────────────────────────────────


class OtelQueryBackend(Protocol):
    """Read-only span query surface.

    Reference dev-mode impl (``JsonlOtelBackend``) reads the local JSONL
    trace file monet writes when ``MONET_TRACE_FILE`` is set. Production
    replaces it with a Langfuse / Datadog / Honeycomb / Jaeger adapter that
    implements the same two methods — same agent code, different backend.
    """

    async def query_spans(
        self,
        *,
        agent_id: str | None,
        run_id: str | None,
        since: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return spans matching the given filters, newest first."""
        ...

    async def token_usage(
        self, *, agent_id: str, since: str | None
    ) -> dict[str, float]:
        """Return ``{input_tokens, output_tokens, total_tokens}`` for the
        given agent over the time window. Aggregates across child LLM
        spans of the agent's root span — that aggregation is the whole
        reason this tool exists rather than trying to read tokens off
        the hook-visible agent span.
        """
        ...

    async def agent_invocations(
        self, *, agent_id: str, command: str | None, since: str | None
    ) -> list[dict[str, Any]]:
        """Return one dict per ``agent.<id>.<command>`` span in the window.

        Each dict has ``run_id``, ``trace_id``, ``success``,
        ``duration_ms``, ``signals`` (list of ``{type, reason}`` from
        span events), and ``command``. This is the per-invocation record
        that an earlier version of the example kept as ``RunSummary``
        artifacts; deriving it from spans removes the duplicate store.
        """
        ...


class JsonlOtelBackend:
    """Dev-mode OTel backend — reads a local JSONL trace file.

    Monet's ``configure_tracing()`` can be pointed at a file exporter via
    ``MONET_TRACE_FILE``. Each line is one span serialised as JSON
    (attributes inline). This backend scans the file lazily; it is
    intentionally dumb — a real span store (Langfuse, Datadog) replaces
    it entirely.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        resolved = path or os.environ.get("MONET_TRACE_FILE")
        self._path = Path(resolved) if resolved else None

    def _iter_spans(self) -> list[dict[str, Any]]:
        if self._path is None or not self._path.exists():
            return []
        spans: list[dict[str, Any]] = []
        with self._path.open(encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    spans.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return spans

    def _attr(self, span: dict[str, Any], key: str) -> Any:
        attrs = span.get("attributes") or {}
        return attrs.get(key)

    async def query_spans(
        self,
        *,
        agent_id: str | None,
        run_id: str | None,
        since: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        rows = self._iter_spans()
        if agent_id is not None:
            rows = [s for s in rows if self._attr(s, "agent.id") == agent_id]
        if run_id is not None:
            rows = [s for s in rows if self._attr(s, "monet.run_id") == run_id]
        if since is not None:
            rows = [s for s in rows if (s.get("start_time") or "") >= since]
        rows.sort(key=lambda s: s.get("start_time") or "", reverse=True)
        return rows[:limit]

    async def token_usage(
        self, *, agent_id: str, since: str | None
    ) -> dict[str, float]:
        spans = await self.query_spans(
            agent_id=None, run_id=None, since=since, limit=100_000
        )
        # Walk every span, sum gen_ai.usage.* for those whose parent chain
        # belongs to the target agent. Dev-mode JSONL does not carry a
        # parent-chain, so we match on gen_ai attributes directly and use
        # agent_id on the same span when present. Imperfect but honest for
        # a dev backend.
        totals = {"input_tokens": 0.0, "output_tokens": 0.0, "total_tokens": 0.0}
        for span in spans:
            if self._attr(span, "agent.id") not in (agent_id, None):
                continue
            for key, bucket in (
                ("gen_ai.usage.input_tokens", "input_tokens"),
                ("gen_ai.usage.output_tokens", "output_tokens"),
                ("gen_ai.usage.total_tokens", "total_tokens"),
            ):
                value = self._attr(span, key)
                if isinstance(value, int | float):
                    totals[bucket] += float(value)
        return totals

    async def agent_invocations(
        self, *, agent_id: str, command: str | None, since: str | None
    ) -> list[dict[str, Any]]:
        spans = await self.query_spans(
            agent_id=agent_id, run_id=None, since=since, limit=100_000
        )
        rows: list[dict[str, Any]] = []
        for span in spans:
            span_command = self._attr(span, "agent.command") or ""
            if command is not None and span_command != command:
                continue
            signals: list[dict[str, Any]] = []
            for event in span.get("events") or []:
                if event.get("name") == "signal":
                    event_attrs = event.get("attributes") or {}
                    signals.append(
                        {
                            "type": event_attrs.get("signal.type", ""),
                            "reason": event_attrs.get("signal.reason", ""),
                        }
                    )
            rows.append(
                {
                    "run_id": self._attr(span, "monet.run_id") or "",
                    "trace_id": self._attr(span, "monet.trace_id") or "",
                    "command": span_command,
                    "success": bool(self._attr(span, "agent.success")),
                    "duration_ms": self._duration_ms(span),
                    "signals": signals,
                }
            )
        return rows

    @staticmethod
    def _duration_ms(span: dict[str, Any]) -> float:
        start = span.get("start_time")
        end = span.get("end_time")
        if not isinstance(start, str) or not isinstance(end, str):
            return 0.0
        try:
            from datetime import datetime

            return (
                datetime.fromisoformat(end.replace("Z", "+00:00"))
                - datetime.fromisoformat(start.replace("Z", "+00:00"))
            ).total_seconds() * 1000.0
        except ValueError:
            return 0.0


_default_otel_backend: OtelQueryBackend | None = None


def configure_otel_backend(backend: OtelQueryBackend | None) -> None:
    """Wire a custom OTel backend. Pass None to reset (defaults to JSONL).

    User pipelines swap in a Langfuse / Datadog / Honeycomb adapter at
    startup; the ``otel_query`` tool stays unchanged.
    """
    global _default_otel_backend
    _default_otel_backend = backend


def _get_backend() -> OtelQueryBackend:
    if _default_otel_backend is not None:
        return _default_otel_backend
    return JsonlOtelBackend()


@tool
async def otel_query(
    *,
    agent_id: str | None = None,
    run_id: str | None = None,
    since: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Query OTel spans for the configured backend.

    Args:
        agent_id: Only return spans with ``attributes.agent.id`` matching.
        run_id: Only return spans with ``attributes.monet.run_id`` matching.
        since: ISO-8601 UTC timestamp — only spans that started at or after.
        limit: Max rows returned, ordered newest first (default 100).

    Returns a list of span dicts (shape depends on the backend; JSONL
    backend preserves the exporter's serialisation). Use with
    ``artifact_query`` to join span telemetry with artifact-level
    outcomes.
    """
    return await _get_backend().query_spans(
        agent_id=agent_id, run_id=run_id, since=since, limit=limit
    )


@tool
async def otel_agent_invocations(
    agent_id: str, command: str | None = None, since: str | None = None
) -> list[dict[str, Any]]:
    """Per-invocation rows for a given agent derived from OTel spans.

    Args:
        agent_id: The agent whose invocations to list.
        command: Narrow to a single command (e.g. ``"deep"``), optional.
        since: ISO-8601 UTC timestamp — lower bound on span start time.

    Returns a list of ``{run_id, trace_id, command, success, duration_ms,
    signals}`` dicts, one per ``agent.<id>.<command>`` span. ``signals``
    contains ``{type, reason}`` pairs reconstructed from span events
    (emitted by ``emit_signal``). This is the single source of truth for
    per-invocation outcomes — do not persist a separate run-summary
    artifact alongside it.
    """
    return await _get_backend().agent_invocations(
        agent_id=agent_id, command=command, since=since
    )


@tool
async def otel_token_usage(agent_id: str, since: str | None = None) -> dict[str, float]:
    """Aggregate ``gen_ai.usage.*`` token counts for an agent over a window.

    Args:
        agent_id: Required. The agent whose tokens to sum.
        since: ISO-8601 UTC timestamp — lower bound on span start time.

    Returns ``{input_tokens, output_tokens, total_tokens}``. Tokens come
    from child LLM-call spans (gen_ai.usage.* attributes emitted by
    LangChain / provider SDKs with OTel instrumentation enabled), which
    the agent's own span cannot see — this is why token accounting lives
    behind a tool rather than inline on RunSummary.
    """
    return await _get_backend().token_usage(agent_id=agent_id, since=since)


__all__ = [
    "JsonlOtelBackend",
    "OtelQueryBackend",
    "artifact_query",
    "configure_otel_backend",
    "otel_agent_invocations",
    "otel_query",
    "otel_token_usage",
]
