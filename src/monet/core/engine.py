"""Agent execution engine — single-task execution lifecycle.

Boundary:
  Worker (_loop.py) — claim loop, semaphore, heartbeat, progress drain
    |
  Engine (engine.py) — execute_task(): context setup, lifecycle events,
      handler dispatch, timeout, complete/fail
    |
  enter_agent_run() — agent runtime: hooks, context vars, OTel agent span,
      param injection, result wrapping, exception translation
    |
  Agent.fn(**kwargs) — user function

Lifecycle events (agent_started, agent_completed, agent_failed, hitl_cause)
are emitted by execute_task() wrapping the enter_agent_run() call.

Import constraint: MUST NOT import from monet.orchestration, monet.worker, or langgraph.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
from typing import TYPE_CHECKING, Any

from opentelemetry import trace

from monet.exceptions import EscalationRequired, NeedsHumanReview, SemanticError
from monet.types import (
    AgentResult,
    AgentRunContext,
    ArtifactPointer,
    Signal,
    SignalType,
)

from .artifacts import _artifact_collector, _artifact_hashes, get_artifacts
from .context import _agent_context
from .hooks import run_after_agent_hooks, run_before_agent_hooks
from .stubs import _signal_collector
from .tracing import get_tracer

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from monet.core.decorator import Agent
    from monet.core.registry import LocalRegistry
    from monet.events import TaskRecord
    from monet.progress._protocol import ProgressWriter
    from monet.queue import TaskQueue

logger = logging.getLogger("monet.engine")
_tracer = trace.get_tracer("monet.worker")

#: Default content limit for automatic artifact offload (bytes).
DEFAULT_CONTENT_LIMIT = 4000

#: error_type metadata value for the empty-result poka-yoke signal.
EMPTY_AGENT_RESULT_ERROR_TYPE = "empty_agent_result"

# Fields available for injection from AgentRunContext.
_CONTEXT_FIELDS: frozenset[str] = frozenset(AgentRunContext.__annotations__.keys())


async def _record_lifecycle(
    run_id: str,
    task_id: str,
    agent_id: str,
    event_type_str: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Record a lifecycle ProgressEvent. Best-effort — never raises."""
    try:
        import time as _time

        from monet.core.stubs import _progress_writer_cv

        pw = _progress_writer_cv.get()
        if pw is None:
            return
        from monet.events import EventType, ProgressEvent

        event: ProgressEvent = {
            "event_id": 0,
            "run_id": run_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "event_type": EventType(event_type_str),
            "timestamp_ms": int(_time.time() * 1000),
        }
        if payload:
            event["payload"] = payload
        await pw.record(run_id, event)
    except Exception:
        pass


def _validate_signature(fn: Callable[..., Any], agent_id: str) -> None:
    """Validate function parameters against AgentRunContext fields.

    Raises TypeError at decoration time (not call time) if any parameter
    name is not a valid AgentRunContext field.
    """
    sig = inspect.signature(fn)
    for param_name in sig.parameters:
        if param_name not in _CONTEXT_FIELDS:
            msg = (
                f"Parameter '{param_name}' on {fn.__qualname__} "
                f"(agent_id='{agent_id}') is not a valid AgentRunContext "
                f"field. Valid fields: {sorted(_CONTEXT_FIELDS)}"
            )
            raise TypeError(msg)


def _inject_params(fn: Callable[..., Any], ctx: AgentRunContext) -> dict[str, Any]:
    """Build kwargs from context fields matching function parameters."""
    sig = inspect.signature(fn)
    kwargs: dict[str, Any] = {}
    for param_name in sig.parameters:
        kwargs[param_name] = ctx[param_name]  # type: ignore[literal-required]
    return kwargs


async def _wrap_result(
    return_value: Any,
    ctx: AgentRunContext,
    artifacts: list[ArtifactPointer],
    signals: list[Signal],
    written_hashes: set[str],
    allow_empty: bool = False,
    content_limit: int = DEFAULT_CONTENT_LIMIT,
) -> AgentResult:
    """Assemble a successful AgentResult from a function's return value.

    Inline output is ``str | dict | None``. If a string return exceeds
    ``content_limit`` and an artifact backend is configured, the full
    content is offloaded to the artifact store (the pointer lands in
    ``artifacts``) and ``output`` becomes a short inline summary.

    Poka-yoke guard: when ``allow_empty`` is ``False`` (the default),
    a string/None return that leaves ``output`` effectively empty AND
    produces no artifacts is treated as a defect — the result is
    downgraded to ``success=False`` with a ``SEMANTIC_ERROR`` signal
    of type ``empty_agent_result``. Dict returns are exempt because
    structured outputs are legitimately small. Agents that legitimately
    return nothing (e.g. signal-only ack handlers) must opt out with
    ``@agent(..., allow_empty=True)``.
    """
    output: str | dict[str, Any] | None
    if return_value is None:
        output = None
    elif isinstance(return_value, dict):
        output = return_value
    else:
        output_str = str(return_value)
        output = output_str
        if len(output_str) > content_limit:
            from .artifacts import has_backend

            encoded = output_str.encode()
            already_written = hashlib.sha256(encoded).hexdigest() in written_hashes
            if already_written:
                # Agent already persisted exact bytes explicitly — suppress
                # the auto-offload but keep the full return value inline.
                # The agent chose to return this content; truncating would
                # silently drop data a chat transcript / direct consumer
                # expects to see. Large payloads are the agent's own
                # responsibility to shrink (e.g. by returning a summary).
                output = output_str
            elif has_backend():
                try:
                    # write() appends to _artifact_collector (same list as `artifacts`)
                    await get_artifacts().write(
                        content=output_str.encode(),
                        content_type="text/plain",
                        summary=output_str[:200],
                        confidence=0.0,
                        completeness="complete",
                    )
                    output = output_str[:200]
                except NotImplementedError:
                    pass

    # Poka-yoke: surface empty string/None results with no artifacts as a
    # defect so the execution graph / review interface can't silently
    # treat them as success. Dicts are exempt (structured outputs may
    # legitimately be tiny) and ``allow_empty`` opts out entirely.
    if not allow_empty and not isinstance(output, dict) and not artifacts:
        is_empty = output is None or (isinstance(output, str) and not output.strip())
        if is_empty:
            signals.append(
                Signal(
                    type=SignalType.SEMANTIC_ERROR,
                    reason=(
                        "Agent returned empty output and wrote no artifacts. "
                        "This usually means an upstream call failed silently. "
                        "If the agent is intentionally signal-only, decorate "
                        "it with @agent(..., allow_empty=True)."
                    ),
                    metadata={"error_type": EMPTY_AGENT_RESULT_ERROR_TYPE},
                )
            )
            return AgentResult(
                success=False,
                output="",
                artifacts=tuple(artifacts),
                signals=tuple(signals),
                trace_id=ctx.get("trace_id", ""),
                run_id=ctx.get("run_id", ""),
            )

    return AgentResult(
        success=True,
        output=output,
        artifacts=tuple(artifacts),
        signals=tuple(signals),
        trace_id=ctx.get("trace_id", ""),
        run_id=ctx.get("run_id", ""),
    )


def _handle_exception(
    exc: Exception,
    ctx: AgentRunContext,
    artifacts: list[ArtifactPointer],
    signals: list[Signal],
    cause_id: str | None = None,
) -> AgentResult:
    """Translate typed or unexpected exceptions into AgentResult with signals.

    Appends an appropriate signal to the accumulated list, then builds
    a failed AgentResult. cause_id, when provided, is embedded in the
    signal metadata for HITL exceptions so it can flow to the interrupt payload.
    """
    if isinstance(exc, NeedsHumanReview):
        signals.append(
            Signal(
                type=SignalType.NEEDS_HUMAN_REVIEW,
                reason=exc.reason,
                metadata={"cause_id": cause_id} if cause_id else None,
            )
        )
    elif isinstance(exc, EscalationRequired):
        signals.append(
            Signal(
                type=SignalType.ESCALATION_REQUIRED,
                reason=exc.reason,
                metadata={"cause_id": cause_id} if cause_id else None,
            )
        )
    elif isinstance(exc, SemanticError):
        signals.append(
            Signal(
                type=SignalType.SEMANTIC_ERROR,
                reason=exc.message,
                metadata={"error_type": exc.type},
            )
        )
    else:
        signals.append(
            Signal(
                type=SignalType.SEMANTIC_ERROR,
                reason=str(exc),
                metadata={"error_type": "unexpected_error"},
            )
        )

    return AgentResult(
        success=False,
        output="",
        artifacts=tuple(artifacts),
        signals=tuple(signals),
        trace_id=ctx.get("trace_id", ""),
        run_id=ctx.get("run_id", ""),
    )


async def enter_agent_run(agent: Agent, ctx: AgentRunContext) -> AgentResult:
    """Execute an agent function in a fully prepared runtime context.

    Owns: before/after hooks, ContextVar setup/teardown, OTel agent span,
    parameter injection, result wrapping, exception translation.
    Called by execute_task() for Agent instances and by Agent.__call__()
    for direct invocations (tests, non-queue paths).
    """
    artifacts: list[ArtifactPointer] = []
    signal_list: list[Signal] = []
    written_hashes: set[str] = set()
    tracer = get_tracer("monet.agent")

    # before_agent hooks run before context vars are set so hooks see the
    # raw incoming context. Hook failure is fatal: agent never runs.
    try:
        ctx = await run_before_agent_hooks(ctx, agent.agent_id, agent.command)
    except Exception as hook_exc:
        return _handle_exception(hook_exc, ctx, artifacts, signal_list)

    ctx_token = _agent_context.set(ctx)
    sig_token = _signal_collector.set(signal_list)
    art_token = _artifact_collector.set(artifacts)
    hash_token = _artifact_hashes.set(written_hashes)
    try:
        with tracer.start_as_current_span(
            f"agent.{agent.agent_id}.{agent.command}",
            attributes={
                "agent.id": agent.agent_id,
                "agent.command": agent.command,
                "monet.run_id": ctx.get("run_id", ""),
            },
        ) as span:
            try:
                kwargs = _inject_params(agent.fn, ctx)
                if asyncio.iscoroutinefunction(agent.fn):
                    result = await agent.fn(**kwargs)
                else:
                    result = agent.fn(**kwargs)
                agent_result = await _wrap_result(
                    result,
                    ctx,
                    artifacts,
                    signal_list,
                    written_hashes,
                    allow_empty=agent.allow_empty,
                )
                span.set_attribute("agent.success", agent_result.success)
                agent_result = await run_after_agent_hooks(
                    agent_result, agent.agent_id, agent.command
                )
                return agent_result
            except Exception as exc:
                cause_id: str | None = None
                if isinstance(exc, NeedsHumanReview | EscalationRequired):
                    import uuid as _uuid

                    cause_id = str(_uuid.uuid4())
                agent_result = _handle_exception(
                    exc, ctx, artifacts, signal_list, cause_id
                )
                span.set_attribute("agent.success", False)
                span.record_exception(exc)
                agent_result = await run_after_agent_hooks(
                    agent_result, agent.agent_id, agent.command
                )
                return agent_result
    finally:
        _artifact_hashes.reset(hash_token)
        _artifact_collector.reset(art_token)
        _signal_collector.reset(sig_token)
        _agent_context.reset(ctx_token)


async def execute_task(
    record: TaskRecord,
    registry: LocalRegistry,
    queue: TaskQueue,
    *,
    publisher: Callable[[dict[str, Any]], None] | None = None,
    writer: ProgressWriter | None = None,
    pool: str = "local",
    task_timeout: float = 300.0,
    on_before_complete: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Execute a single task record: set context, run handler, complete or fail.

    Args:
        record: The task to execute.
        registry: Handler registry to look up the agent handler.
        queue: Queue to call complete() or fail() on.
        publisher: Optional sync callable receiving progress event dicts.
            Forwarded to _progress_publisher ContextVar.
        writer: Optional ProgressWriter forwarded to _progress_writer_cv ContextVar.
        pool: Pool name, recorded on the OTel span.
        task_timeout: Max seconds the handler may run before timeout failure.
        on_before_complete: Async callback invoked before every queue.complete()
            or queue.fail() call. Used by the worker to flush the progress drain
            BEFORE completing — completing terminates progress subscriptions.
    """
    from monet.core.stubs import (
        _current_task_id,
        _progress_publisher,
        _progress_writer_cv,
    )

    task_id = record["task_id"]
    agent_id = record["agent_id"]
    command = record["command"]
    run_id = record["context"].get("run_id", "")

    token = _progress_publisher.set(publisher)
    writer_token = _progress_writer_cv.set(writer)
    task_id_token = _current_task_id.set(task_id)

    async def _before_complete() -> None:
        if on_before_complete is not None:
            await on_before_complete()

    try:
        with _tracer.start_as_current_span(
            f"worker.execute.{agent_id}.{command}",
            attributes={
                "agent.id": agent_id,
                "agent.command": command,
                "worker.pool": pool,
                "task.id": task_id,
            },
        ):
            handler = registry.lookup(agent_id, command)
            if handler is None:
                logger.warning(
                    "worker: no handler for %s/%s (task %s)",
                    agent_id,
                    command,
                    task_id,
                )
                await queue.fail(
                    task_id,
                    f"No handler for {agent_id}/{command} in worker registry",
                )
                return
            logger.info(
                "worker: executing %s/%s task=%s pool=%s",
                agent_id,
                command,
                task_id,
                pool,
            )
            await _record_lifecycle(run_id, task_id, agent_id, "agent_started")
            try:
                from monet.core.decorator import Agent

                if isinstance(handler, Agent):
                    coro = enter_agent_run(handler, record["context"])
                else:
                    coro = handler(record["context"])
                result = await asyncio.wait_for(coro, timeout=task_timeout)

                # Inspect for HITL cause_id before classifying success/failure.
                for sig in result.signals:
                    if sig["type"] in (
                        SignalType.NEEDS_HUMAN_REVIEW,
                        SignalType.ESCALATION_REQUIRED,
                    ):
                        cause_id = (sig.get("metadata") or {}).get("cause_id", "")
                        await _record_lifecycle(
                            run_id,
                            task_id,
                            agent_id,
                            "hitl_cause",
                            payload={"cause_id": cause_id},
                        )
                        break

                lifecycle_event = (
                    "agent_completed" if result.success else "agent_failed"
                )
                await _record_lifecycle(run_id, task_id, agent_id, lifecycle_event)

                await _before_complete()
                await queue.complete(task_id, result)
                logger.info(
                    "worker: completed %s/%s task=%s success=%s",
                    agent_id,
                    command,
                    task_id,
                    result.success,
                )
            except TimeoutError:
                logger.warning(
                    "worker: task %s timed out after %ss (%s/%s)",
                    task_id,
                    task_timeout,
                    agent_id,
                    command,
                )
                await _record_lifecycle(run_id, task_id, agent_id, "agent_failed")
                await _before_complete()
                await queue.fail(
                    task_id,
                    f"Task execution timed out after {task_timeout}s",
                )
            except Exception as exc:
                logger.exception(
                    "Worker: unhandled exception executing %s/%s",
                    agent_id,
                    command,
                )
                await _record_lifecycle(run_id, task_id, agent_id, "agent_failed")
                await _before_complete()
                await queue.fail(task_id, f"{type(exc).__name__}: {exc}")
    finally:
        _progress_publisher.reset(token)
        _progress_writer_cv.reset(writer_token)
        _current_task_id.reset(task_id_token)
