# Claude Code Instructions — SDK Restructure

These instructions cover all changes needed to the monet SDK structure, packaging, public API, catalogue standardisation, observability, and LangGraph Server configuration. Apply them in order. Each section is self-contained.

---

## 1. Package structure and `pyproject.toml`

### 1.1 Remove FastAPI and uvicorn

Remove `fastapi` and `uvicorn` from dependencies entirely. Remove `src/monet/server/` directory if it exists. The server is LangGraph Server — we do not build our own.

### 1.2 Final `pyproject.toml`

```toml
[project]
name = "monet"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    # core SDK — observability
    "opentelemetry-api>=1.20",
    "opentelemetry-sdk>=1.20",
    "opentelemetry-exporter-otlp-proto-http>=1.20",
    # orchestration — part of the base proposition, not optional
    "langgraph>=1.0",
    "langgraph-checkpoint-postgres>=1.0",
    "psycopg[binary]>=3.0",
    # http agent adapters — invoke_agent() HTTP path
    "httpx>=0.27",
    # catalogue reference implementation
    "sqlalchemy>=2.0",
    "aiofiles>=23.0",
]

[project.optional-dependencies]
agents = [
    "langchain-core>=0.3",
]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "hypothesis>=6.0",
    "ruff>=0.4",
    "mypy>=1.9",
    "langgraph-cli>=0.1",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = [
    "integration: requires docker-compose services (postgres)",
    "llm_integration: requires LLM API keys",
]
```

### 1.3 Source layout

```
src/monet/
    __init__.py              # public API — all exports, nothing else
    _decorator.py            # @agent decorator — internal
    _registry.py             # AgentRegistry — internal
    _context.py              # ContextVar machinery — internal
    _catalogue.py            # get_catalogue_writer(), _set_catalogue_backend(),
                             # CatalogueWriter, artifact collector — internal
    _stubs.py                # emit_progress, emit_signal, signal collector — internal
    _tracing.py              # OTel setup — internal
    types.py                 # AgentResult, AgentRunContext, Signal,
                             # SignalType, ArtifactPointer
    exceptions.py            # NeedsHumanReview, EscalationRequired, SemanticError
    catalogue/
        __init__.py          # public exports including handle_agent_event
        _protocol.py         # CatalogueClient Protocol — the interface standard
        _metadata.py         # ArtifactMetadata TypedDict
        _storage.py          # FilesystemStorage — reference implementation
        _index.py            # SQLiteIndex — reference implementation
        _service.py          # CatalogueService — wires storage + index
        _memory.py           # InMemoryCatalogueClient — for tests only
        _events.py           # handle_agent_event() — for wrapper developers
    orchestration/
        __init__.py          # exports invoke_agent, build_* functions, run
        _state.py            # TypedDict state schemas, has_signal, get_signal
        _invoke.py           # invoke_agent() implementation
        _content_limit.py    # enforce_content_limit()
        _retry.py            # RetryPolicy from CommandDescriptor
        entry_graph.py       # triage graph
        planning_graph.py    # planning loop and HITL approval
        execution_graph.py   # wave execution via Send + QA reflection
        kaizen.py            # post-execution hook
        _run.py              # top-level run() sequencer
    agents/                  # monet[agents] — reference implementations
        __init__.py          # registers all agents on import
        planner.py
        researcher.py
        writer.py
        qa.py
        publisher.py
        analyst.py
    __main__.py              # python -m monet — direct CLI (not server)
```

**Hard rule enforced in CI**: Nothing outside `src/monet/` ever imports from a `monet._*` module. If application code needs something from an underscored module, add it to `monet/__init__.py`. Verify with:

```bash
grep -r "from monet\._" examples/ tests/ --include="*.py"
# Must return nothing
```

---

## 2. `monet/__init__.py` — complete public API

Three public submodules with earned boundaries:
- `monet` root — everything an agent developer needs. One import location, no decisions.
- `monet.catalogue` — infrastructure. Developers who bring their own backend work here.
- `monet.orchestration` — graph layer. Developers who invoke agents directly or build on the graphs.

```python
"""
monet — multi-agent orchestration SDK.

Install:
    pip install monet            # SDK + orchestration + catalogue reference
    pip install monet[agents]    # + reference agent implementations

Quick start:
    from monet import agent, emit_progress, get_catalogue_writer
    from monet.catalogue import CatalogueService, FilesystemStorage, SQLiteIndex
    from monet.orchestration import invoke_agent

    # Configure catalogue once at startup
    from monet._catalogue import _set_catalogue_backend
    _set_catalogue_backend(CatalogueService(
        storage=FilesystemStorage(".catalogue/artifacts"),
        index=SQLiteIndex("sqlite+aiosqlite:///.catalogue/index.db"),
    ))

    @agent(agent_id="my-agent")
    async def my_agent_fast(task: str, context: list):
        \"\"\"Do something useful.\"\"\"
        emit_progress({"status": "working"})
        catalogue = get_catalogue_writer()
        ptr = await catalogue.write(
            content=b"result content",
            content_type="text/plain",
            summary="result",
            confidence=0.9,
            completeness="complete",
        )
        return ptr
"""
from monet._decorator import agent
from monet.types import (
    AgentResult,
    AgentRunContext,
    ArtifactPointer,
    Signal,
    SignalType,
)
from monet._context import get_run_context, get_run_logger
from monet._catalogue import get_catalogue_writer
from monet._stubs import emit_progress, emit_signal
from monet.exceptions import NeedsHumanReview, EscalationRequired, SemanticError

__all__ = [
    # decorator
    "agent",
    # types
    "AgentResult", "AgentRunContext", "ArtifactPointer",
    "Signal", "SignalType",
    # three context-aware getters — consistent pattern
    "get_run_context",       # → AgentRunContext
    "get_run_logger",        # → logger
    "get_catalogue_writer",  # → CatalogueWriter
    # progress and signals
    "emit_progress", "emit_signal",
    # exceptions
    "NeedsHumanReview", "EscalationRequired", "SemanticError",
]
```

Two things deliberately not in the root `__all__`:

- `invoke_agent()` — from `monet.orchestration`, not the root. It is an orchestration concern.
- `handle_agent_event()` — from `monet.catalogue`. It is a utility for non-Python wrapper developers, not a core agent authoring primitive.

```python
from monet.orchestration import invoke_agent
from monet.catalogue import handle_agent_event  # non-Python wrapper developers only
```

**The three-getter pattern** — consistent across all context-aware SDK access:

```python
# Inside any @agent decorated function (or anywhere in the call stack beneath it)
from monet import get_run_context, get_run_logger, get_catalogue_writer

ctx       = get_run_context()       # AgentRunContext — run_id, trace_id, agent_id
logger    = get_run_logger()        # structured logger → OTel spans
catalogue = get_catalogue_writer()  # CatalogueWriter → write artifacts

logger.info("starting", run_id=ctx.run_id)
ptr = await catalogue.write(content=..., content_type=..., summary=...,
                             confidence=0.9, completeness="complete")
```

All three raise a clear error if called outside the decorator context. All three are no-ops or safe in tests when configured appropriately.

---

## 3. `types.py` — all public types

```python
from enum import Enum
from typing import TypedDict, Optional

class SignalType(str, Enum):
    NEEDS_HUMAN_REVIEW  = "needs_human_review"
    ESCALATION_REQUIRED = "escalation_required"
    LOW_CONFIDENCE      = "low_confidence"
    PARTIAL_RESULT      = "partial_result"
    REVISION_SUGGESTED  = "revision_suggested"
    SENSITIVE_CONTENT   = "sensitive_content"
    SEMANTIC_ERROR      = "semantic_error"

class Signal(TypedDict):
    type: str           # SignalType value
    reason: str
    metadata: Optional[dict]

class ArtifactPointer(TypedDict):
    artifact_id: str
    url: str            # file:// | memory:// | s3:// etc.

class AgentRunContext(TypedDict):
    agent_id: str
    command: str
    task: str
    trace_id: str
    run_id: str
    skills: list[str]

class AgentResult(TypedDict):
    success: bool
    output: Optional[str]
    artifacts: list         # list[ArtifactPointer] — accumulates
    signals: list           # list[Signal] — accumulates, never single object
    trace_id: str
    run_id: str
```

---

## 4. Catalogue — protocol and reference implementation

### 4.1 `catalogue/_protocol.py` — the interface standard

The catalogue is a standard with a reference implementation, analogous to OTel. Any object implementing `CatalogueClient` can be used. Production applications bring their own by implementing this protocol against their storage backend of choice.

```python
"""
CatalogueClient protocol — the interface any catalogue implementation must satisfy.

The monet SDK ships a reference implementation (CatalogueService + FilesystemStorage
+ SQLiteIndex) suitable for development and simple deployments.

Production applications implement this protocol against their own backend:
S3, GCS, a content management system, an existing artifact store, etc.

This is analogous to OpenTelemetry: monet defines the interface, the application
chooses the backend.
"""
from typing import Protocol, runtime_checkable, TYPE_CHECKING
from monet.types import ArtifactPointer

if TYPE_CHECKING:
    from monet.catalogue._metadata import ArtifactMetadata

@runtime_checkable
class CatalogueClient(Protocol):
    async def write(
        self,
        content: bytes,
        content_type: str,
        summary: str,
        confidence: float,
        completeness: str,
        sensitivity_label: str = "internal",
        **kwargs,
    ) -> ArtifactPointer: ...

    async def read(
        self,
        artifact_id: str,
    ) -> tuple[bytes, "ArtifactMetadata"]: ...
```

### 4.2 `catalogue/_metadata.py`

```python
from typing import TypedDict, Optional

class ArtifactMetadata(TypedDict):
    artifact_id: str
    content_type: str
    content_length: int
    summary: str
    confidence: float
    completeness: str       # "complete" | "partial" | "resource-bounded"
    sensitivity_label: str  # "public" | "internal" | "confidential"
    agent_id: Optional[str]
    run_id: Optional[str]
    trace_id: Optional[str]
    tags: dict
    created_at: str         # ISO 8601
```

### 4.3 `catalogue/_storage.py` — `FilesystemStorage`

Writes bytes to a local filesystem directory. Each artifact gets a subdirectory under `root/` keyed by `artifact_id` containing `content` (bytes) and `meta.json`. Simple, inspectable, no external dependencies beyond `aiofiles`.

```python
import json
from pathlib import Path
import aiofiles
from monet.catalogue._metadata import ArtifactMetadata
from monet.types import ArtifactPointer

class FilesystemStorage:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    async def write(
        self, content: bytes, metadata: ArtifactMetadata
    ) -> ArtifactPointer:
        artifact_id = metadata["artifact_id"]
        artifact_dir = self.root / artifact_id
        artifact_dir.mkdir(parents=True, exist_ok=True)

        async with aiofiles.open(artifact_dir / "content", "wb") as f:
            await f.write(content)
        async with aiofiles.open(artifact_dir / "meta.json", "w") as f:
            await f.write(json.dumps(metadata, indent=2))

        return ArtifactPointer(
            artifact_id=artifact_id,
            url=f"file://{artifact_dir / 'content'}",
        )

    async def read(self, artifact_id: str) -> tuple[bytes, ArtifactMetadata]:
        artifact_dir = self.root / artifact_id
        if not artifact_dir.exists():
            raise KeyError(f"Artifact not found: {artifact_id}")
        async with aiofiles.open(artifact_dir / "content", "rb") as f:
            content = await f.read()
        async with aiofiles.open(artifact_dir / "meta.json") as f:
            metadata = json.loads(await f.read())
        return content, metadata
```

### 4.4 `catalogue/_index.py` — `SQLiteIndex`

SQLite index for querying artifacts by `run_id`, `agent_id`, `content_type` etc. Uses SQLAlchemy with the `aiosqlite` async driver. Note: db_url must use `sqlite+aiosqlite://` scheme.

```python
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import DeclarativeBase, mapped_column, Mapped
from sqlalchemy import String, Float, Integer, Text, select, JSON
from monet.catalogue._metadata import ArtifactMetadata

class Base(DeclarativeBase):
    pass

class ArtifactRecord(Base):
    __tablename__ = "artifacts"
    artifact_id: Mapped[str] = mapped_column(String, primary_key=True)
    content_type: Mapped[str] = mapped_column(String)
    content_length: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    completeness: Mapped[str] = mapped_column(String)
    sensitivity_label: Mapped[str] = mapped_column(String)
    agent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String, nullable=True)
    tags: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String)

class SQLiteIndex:
    def __init__(self, db_url: str = "sqlite+aiosqlite:///.catalogue/index.db"):
        self._engine = create_async_engine(db_url)

    async def initialise(self) -> None:
        """Create tables. Call once at startup."""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def put(self, metadata: ArtifactMetadata) -> None:
        async with AsyncSession(self._engine) as session:
            session.add(ArtifactRecord(**metadata))
            await session.commit()

    async def get(self, artifact_id: str) -> ArtifactMetadata | None:
        async with AsyncSession(self._engine) as session:
            result = await session.get(ArtifactRecord, artifact_id)
            if result is None:
                return None
            return ArtifactMetadata(**{
                c.key: getattr(result, c.key)
                for c in result.__table__.columns
            })

    async def query_by_run(self, run_id: str) -> list[ArtifactMetadata]:
        async with AsyncSession(self._engine) as session:
            rows = await session.execute(
                select(ArtifactRecord).where(ArtifactRecord.run_id == run_id)
            )
            return [
                ArtifactMetadata(**{c.key: getattr(r, c.key)
                                   for c in r.__table__.columns})
                for r in rows.scalars()
            ]
```

### 4.5 `catalogue/_service.py` — `CatalogueService`

```python
"""
Reference implementation of CatalogueClient.
Wires FilesystemStorage (bytes on disk) with SQLiteIndex (queryable metadata).

Production applications implement CatalogueClient directly against their
own storage backend. This service is for development and simple deployments.
"""
import uuid
from datetime import datetime, timezone
from monet.catalogue._storage import FilesystemStorage
from monet.catalogue._index import SQLiteIndex
from monet.catalogue._metadata import ArtifactMetadata
from monet.types import ArtifactPointer

class CatalogueService:
    def __init__(self, storage: FilesystemStorage, index: SQLiteIndex):
        self._storage = storage
        self._index = index

    async def initialise(self) -> None:
        """Call at startup to ensure index tables exist."""
        await self._index.initialise()

    async def write(
        self,
        content: bytes,
        content_type: str,
        summary: str,
        confidence: float,
        completeness: str,
        sensitivity_label: str = "internal",
        **kwargs,
    ) -> ArtifactPointer:
        # get run context if available — not required
        try:
            from monet._context import get_run_context
            ctx = get_run_context()
            run_id, trace_id, agent_id = ctx.run_id, ctx.trace_id, ctx.agent_id
        except Exception:
            run_id = trace_id = agent_id = None

        artifact_id = str(uuid.uuid4())
        metadata = ArtifactMetadata(
            artifact_id=artifact_id,
            content_type=content_type,
            content_length=len(content),
            summary=summary,
            confidence=confidence,
            completeness=completeness,
            sensitivity_label=sensitivity_label,
            agent_id=agent_id,
            run_id=run_id,
            trace_id=trace_id,
            tags=kwargs.get("tags", {}),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        pointer = await self._storage.write(content, metadata)
        await self._index.put(metadata)
        return pointer

    async def read(self, artifact_id: str) -> tuple[bytes, ArtifactMetadata]:
        return await self._storage.read(artifact_id)
```

### 4.6 `catalogue/_memory.py` — `InMemoryCatalogueClient`

```python
"""In-memory catalogue for unit tests only. Not for production."""
import uuid
from datetime import datetime, timezone
from monet.types import ArtifactPointer
from monet.catalogue._metadata import ArtifactMetadata

class InMemoryCatalogueClient:
    def __init__(self):
        self._store: dict[str, tuple[bytes, ArtifactMetadata]] = {}

    async def write(
        self, content: bytes, content_type: str, summary: str,
        confidence: float, completeness: str,
        sensitivity_label: str = "internal", **kwargs,
    ) -> ArtifactPointer:
        artifact_id = str(uuid.uuid4())
        metadata = ArtifactMetadata(
            artifact_id=artifact_id,
            content_type=content_type,
            content_length=len(content),
            summary=summary,
            confidence=confidence,
            completeness=completeness,
            sensitivity_label=sensitivity_label,
            agent_id=None, run_id=None, trace_id=None,
            tags=kwargs.get("tags", {}),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._store[artifact_id] = (content, metadata)
        return ArtifactPointer(
            artifact_id=artifact_id,
            url=f"memory://{artifact_id}",
        )

    async def read(self, artifact_id: str) -> tuple[bytes, ArtifactMetadata]:
        if artifact_id not in self._store:
            raise KeyError(f"Artifact not found: {artifact_id}")
        return self._store[artifact_id]
```

### 4.7 `catalogue/__init__.py`

```python
from monet.catalogue._protocol import CatalogueClient
from monet.catalogue._service import CatalogueService
from monet.catalogue._storage import FilesystemStorage
from monet.catalogue._index import SQLiteIndex
from monet.catalogue._memory import InMemoryCatalogueClient
from monet.catalogue._metadata import ArtifactMetadata
from monet.catalogue._events import handle_agent_event

__all__ = [
    "CatalogueClient",
    "CatalogueService",
    "FilesystemStorage",
    "SQLiteIndex",
    "InMemoryCatalogueClient",
    "ArtifactMetadata",
    "handle_agent_event",   # for non-Python agent wrapper developers
]
```

### 4.8 `catalogue/_events.py` — `handle_agent_event`

For developers writing Python wrappers around non-Python agents (CLI subprocess, HTTP SSE, HTTP polling). Not needed for Python-native agents. Lives in `monet.catalogue` because it handles the `artifact` event type by calling `get_catalogue_writer().write()`.

```python
"""
handle_agent_event() — utility for non-Python agent wrapper developers.

Routes events from the monet agent event vocabulary to the appropriate
SDK function. Use in CLI subprocess loops, SSE streams, and polling loops.

The three wrapper patterns are identical in structure:

    # CLI stdout
    async for line in proc.stdout:
        result = await handle_agent_event(json.loads(line))
        if result is not None:
            return result

    # HTTP SSE
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            result = await handle_agent_event(json.loads(line[5:]))
            if result is not None:
                return result

    # HTTP polling
    while True:
        data = (await client.get(f"/status/{task_id}")).json()
        for event in data.get("events", []):
            result = await handle_agent_event(event)
            if result is not None:
                return result
        if data["status"] == "complete":
            return data.get("result", "")
        await asyncio.sleep(5)
"""
from monet._catalogue import get_catalogue_writer
from monet._stubs import emit_progress, emit_signal
from monet.types import Signal, SignalType
from monet.exceptions import NeedsHumanReview, EscalationRequired, SemanticError

async def handle_agent_event(event: dict) -> str | None:
    """
    Route a monet agent event to the appropriate SDK function.

    Event types:
        progress  → emit_progress()
        artifact  → get_catalogue_writer().write()
        result    → returns the output string (terminal event)
        error     → emit_signal() or raises typed exception
        log       → get_run_logger()

    Returns the result string on 'result' event, None otherwise.
    """
    match event.get("type"):
        case "progress":
            emit_progress({k: v for k, v in event.items() if k != "type"})

        case "artifact":
            catalogue = get_catalogue_writer()
            await catalogue.write(
                content=event["content"].encode()
                    if isinstance(event.get("content"), str)
                    else event.get("content", b""),
                content_type=event.get("content_type", "text/plain"),
                summary=event.get("summary", ""),
                confidence=event.get("confidence", 0.8),
                completeness=event.get("completeness", "complete"),
                sensitivity_label=event.get("sensitivity_label", "internal"),
            )

        case "result":
            return event.get("output", "")

        case "error":
            error_type = event.get("error_type", "semantic_error")
            message = event.get("message", "Agent error")
            if error_type == "needs_human_review":
                raise NeedsHumanReview(reason=message)
            elif error_type == "escalation_required":
                raise EscalationRequired(reason=message)
            else:
                raise SemanticError(type=error_type, message=message)

        case "log":
            from monet._context import get_run_logger
            level = event.get("level", "info")
            getattr(get_run_logger(), level, get_run_logger().info)(
                event.get("message", "")
            )

    return None
```

---

## 5. `_catalogue.py` — catalogue wiring and `CatalogueWriter`

This is internal. The public surface is `get_catalogue_writer()` exported from `monet.__init__`. Application startup calls `_set_catalogue_backend()` once — it is the only place catalogue configuration appears outside of tests.

```python
"""
Catalogue integration for the SDK. Internal.
Public surface: monet.get_catalogue_writer()
Startup: monet._catalogue._set_catalogue_backend(CatalogueService(...))
"""
from __future__ import annotations
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from monet.catalogue._protocol import CatalogueClient
    from monet.types import ArtifactPointer

# ── Backend — configured once at application startup ──────────────────────────

_catalogue_backend: "CatalogueClient | None" = None

def _set_catalogue_backend(client: "CatalogueClient | None") -> None:
    """
    Wire a catalogue implementation. Call once at startup.
    Accepts any object implementing the CatalogueClient protocol.
    Pass None to reset — useful in tests.
    """
    global _catalogue_backend
    if client is not None:
        from monet.catalogue._protocol import CatalogueClient
        if not isinstance(client, CatalogueClient):
            raise TypeError(
                f"{client!r} does not implement CatalogueClient protocol. "
                "Must have async write() and read() methods."
            )
    _catalogue_backend = client

# ── Artifact collector — set by decorator before each invocation ──────────────

_artifact_collector: ContextVar[list | None] = ContextVar(
    "_artifact_collector", default=None
)

# ── CatalogueWriter — returned by get_catalogue_writer() ──────────────────────

class CatalogueWriter:
    """
    Context-aware catalogue writer. Returned by get_catalogue_writer().
    Associates writes with the current run_id and trace_id automatically.
    Registers pointers with the decorator's artifact collection.

    Usage:
        catalogue = get_catalogue_writer()
        ptr = await catalogue.write(
            content=result.encode(),
            content_type="text/markdown",
            summary="Research findings",
            confidence=0.85,
            completeness="complete",
        )
    """
    async def write(
        self,
        content: bytes,
        content_type: str,
        summary: str,
        confidence: float,
        completeness: str,
        sensitivity_label: str = "internal",
        **kwargs,
    ) -> "ArtifactPointer":
        if _catalogue_backend is None:
            raise NotImplementedError(
                "get_catalogue_writer() requires a catalogue backend. "
                "Call monet._catalogue._set_catalogue_backend(CatalogueService(...)) "
                "at startup. In tests: _set_catalogue_backend(InMemoryCatalogueClient())."
            )
        pointer = await _catalogue_backend.write(
            content=content,
            content_type=content_type,
            summary=summary,
            confidence=confidence,
            completeness=completeness,
            sensitivity_label=sensitivity_label,
            **kwargs,
        )
        collector = _artifact_collector.get()
        if collector is not None:
            collector.append(pointer)
        return pointer

_writer_instance = CatalogueWriter()

def get_catalogue_writer() -> CatalogueWriter:
    """
    Return the context-aware catalogue writer.
    One of the three core SDK getters alongside get_run_context() and get_run_logger().
    Works anywhere in the call stack beneath an @agent decorated function.
    """
    return _writer_instance
```

---

## 5b. `_stubs.py` — progress and signal stubs

`_stubs.py` is now smaller — it only owns `emit_progress` and `emit_signal`. Catalogue wiring moved to `_catalogue.py`.

```python
"""
Progress and signal stubs. Internal.
Public surface: monet.emit_progress, monet.emit_signal
"""
from __future__ import annotations
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from monet.types import Signal

# ── Signal collector — set by decorator before each invocation ────────────────

_signal_collector: ContextVar[list | None] = ContextVar(
    "_signal_collector", default=None
)

# ── SDK functions ──────────────────────────────────────────────────────────────

def emit_progress(data: dict) -> None:
    """
    Emit a progress event into the LangGraph stream.
    No-op outside the LangGraph execution context.
    Python 3.11+ required for correct async context propagation.
    """
    try:
        from langgraph.config import get_stream_writer
        writer = get_stream_writer()
        writer(data)
    except Exception:
        pass

def emit_signal(signal: "Signal") -> None:
    """
    Emit a signal alongside the agent result. Non-fatal — agent continues.
    Signals accumulate. No-op outside the @agent decorator context.

    Use NeedsHumanReview / EscalationRequired as exceptions when the agent
    cannot usefully continue. Use emit_signal() when it can return a result
    alongside the signal.
    """
    collector = _signal_collector.get()
    if collector is not None:
        collector.append(signal)
```

---

## 6. `invoke_agent()` — with `**kwargs`

In `src/monet/orchestration/_invoke.py`:

```python
import uuid
from opentelemetry import trace
from monet.types import AgentResult

_RESERVED_FIELDS = {"task", "context", "command", "trace_id", "run_id", "skills"}

async def invoke_agent(
    agent_id: str,
    command: str = "fast",
    task: str = "",
    context: list | None = None,
    trace_id: str | None = None,
    run_id: str | None = None,
    skills: list[str] | None = None,
    **kwargs,
) -> AgentResult:
    """
    Invoke an agent by ID and command.

    Standard envelope fields are explicit parameters. Agent-specific
    parameters pass as **kwargs — injected into the agent function by name
    matching against its signature.

    kwargs flow to the agent. They are not interpreted by the orchestrator.
    Routing is always driven by AgentResult.signals, never by kwargs values.

    kwargs must not shadow reserved envelope fields:
        task, context, command, trace_id, run_id, skills
    """
    conflicts = _RESERVED_FIELDS & set(kwargs)
    if conflicts:
        raise ValueError(
            f"invoke_agent() kwargs conflict with reserved fields: {conflicts}. "
            "Pass these as explicit parameters."
        )

    resolved_run_id = run_id or str(uuid.uuid4())
    resolved_trace_id = trace_id or _generate_trace_id()

    envelope = {
        "task": task,
        "command": command,
        "context": context or [],
        "trace_id": resolved_trace_id,
        "run_id": resolved_run_id,
        "skills": skills or [],
        **kwargs,
    }

    from monet._registry import _default_registry
    tracer = trace.get_tracer("monet.orchestration")

    with tracer.start_as_current_span(
        f"agent.{agent_id}.{command}",
        attributes={
            "agent.id": agent_id,
            "agent.command": command,
            "monet.run_id": resolved_run_id,
        },
    ) as span:
        descriptor = _get_descriptor(agent_id, command)

        if descriptor is None or descriptor.transport == "local":
            fn = _default_registry.lookup(agent_id, command)
            if fn is None:
                raise LookupError(
                    f"No agent registered for '{agent_id}/{command}'. "
                    "Ensure the module containing @agent registrations "
                    "has been imported before invoking."
                )
            import inspect
            params = set(inspect.signature(fn).parameters.keys())
            injected = {k: v for k, v in envelope.items() if k in params}
            result: AgentResult = await fn(**injected)
        else:
            result = await _http_invoke(descriptor, envelope)

        span.set_attribute("agent.success", result["success"])
        span.set_attribute("agent.signal_count", len(result.get("signals", [])))
        return result

def _generate_trace_id() -> str:
    import secrets
    return f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"

def _get_descriptor(agent_id: str, command: str):
    try:
        from monet.descriptors import DescriptorRegistry
        return DescriptorRegistry.get(agent_id, command)
    except Exception:
        return None

async def _http_invoke(descriptor, envelope: dict) -> AgentResult:
    import httpx
    url = f"{descriptor.base_url}/{envelope['command']}"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            headers={"traceparent": envelope["trace_id"]},
            json=envelope,
            timeout=getattr(descriptor, "timeout_seconds", 30),
        )
        response.raise_for_status()
        data = response.json()
    return AgentResult(
        success=data.get("success", True),
        output=data.get("output"),
        artifacts=data.get("artifacts", []),
        signals=data.get("signals", []),
        trace_id=data.get("trace_id", ""),
        run_id=data.get("run_id", ""),
    )
```

In `src/monet/orchestration/__init__.py`:

```python
from monet.orchestration._invoke import invoke_agent
from monet.orchestration.entry_graph import build_entry_graph
from monet.orchestration.planning_graph import build_planning_graph
from monet.orchestration.execution_graph import build_execution_graph
from monet.orchestration._run import run

__all__ = [
    "invoke_agent",
    "build_entry_graph",
    "build_planning_graph",
    "build_execution_graph",
    "run",
]
```

---

## 7. Graph builders — return uncompiled graphs

LangGraph Server calls the builder functions and attaches its own checkpointer. Builders must return an uncompiled `StateGraph`, not a compiled graph.

```python
# entry_graph.py
from langgraph.graph import StateGraph, START, END
from monet.orchestration._state import EntryState

def build_entry_graph() -> StateGraph:
    """Build the entry/triage graph. LangGraph Server compiles this."""
    g = StateGraph(EntryState)
    g.add_node("triage", triage_node)
    g.add_node("responder", responder_node)
    g.add_node("direct_agent", direct_agent_node)
    g.add_edge(START, "triage")
    g.add_conditional_edges("triage", route_from_triage)
    g.add_edge("responder", END)
    g.add_edge("direct_agent", END)
    return g  # NOT g.compile()
```

Same pattern for `build_planning_graph()` and `build_execution_graph()`. The `interrupt_before` configuration goes in the `langgraph.json` or is set when compiling for direct invocation:

```python
# Direct invocation (tests, __main__.py)
from langgraph.checkpoint.memory import MemorySaver
graph = build_planning_graph().compile(
    checkpointer=MemorySaver(),
    interrupt_before=["human_approval"],
)
```

---

## 8. LangGraph Server configuration

Create `langgraph.json` at the repo root:

```json
{
  "dependencies": ["."],
  "graphs": {
    "entry":     "monet.orchestration.entry_graph:build_entry_graph",
    "planning":  "monet.orchestration.planning_graph:build_planning_graph",
    "execution": "monet.orchestration.execution_graph:build_execution_graph"
  },
  "env": ".env"
}
```

Run:

```bash
# Development — Studio UI at http://localhost:8123
langgraph dev

# Production
langgraph up
```

LangGraph Server provides: REST API for runs, SSE streaming, HITL interrupt/resume, Postgres persistence, Studio UI. We build none of this ourselves.

---

## 9. `__main__.py` — direct CLI invocation (not a server)

```python
"""
Run monet orchestration directly without a server.
For development and testing. For production, use: langgraph dev

Usage:
    python -m monet "Write a blog post about AI trends"
    python -m monet    (prompts for input)
"""
import asyncio
import sys

def main() -> None:
    message = " ".join(sys.argv[1:]).strip()
    if not message:
        message = input("Enter message: ").strip()
    if not message:
        print("No message provided.")
        return

    # Startup — configure catalogue
    import os
    from pathlib import Path
    from monet._catalogue import _set_catalogue_backend
    from monet.catalogue import CatalogueService, FilesystemStorage, SQLiteIndex

    catalogue_dir = Path(os.environ.get("MONET_CATALOGUE_DIR", ".catalogue"))
    _set_catalogue_backend(CatalogueService(
        storage=FilesystemStorage(root=catalogue_dir / "artifacts"),
        index=SQLiteIndex(
            db_url=f"sqlite+aiosqlite:///{catalogue_dir}/index.db"
        ),
    ))

    from monet.orchestration import run
    asyncio.run(run(message))

if __name__ == "__main__":
    main()
```

---

## 10. Observability — OTLP backend, not Langfuse-specific

### 10.1 `_tracing.py`

```python
"""
OTel tracing setup. Internal.
The backend is any OTLP-compatible service: Langfuse, LangSmith, SigNoz, etc.
No backend-specific code. Configure via standard OTEL_* environment variables.
"""
import os
import atexit
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.resources import Resource, SERVICE_NAME

_provider: TracerProvider | None = None
_exporter_attached: bool = False

def configure_tracing(
    endpoint: str | None = None,
    service_name: str = "monet",
) -> None:
    """
    Configure OTel tracing. Idempotent — safe to call multiple times.
    Reads OTEL_EXPORTER_OTLP_ENDPOINT and OTEL_SERVICE_NAME from environment.
    """
    global _provider, _exporter_attached

    if _provider is None:
        resource = Resource.create({
            SERVICE_NAME: os.environ.get("OTEL_SERVICE_NAME", service_name),
            "monet.version": "0.1.0",
        })
        _provider = TracerProvider(resource=resource)
        trace.set_tracer_provider(_provider)
        atexit.register(_provider.shutdown)

    ep = endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if ep and not _exporter_attached:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            # No args — exporter reads OTEL_EXPORTER_OTLP_ENDPOINT,
            # OTEL_EXPORTER_OTLP_HEADERS, OTEL_EXPORTER_OTLP_PROTOCOL automatically
            _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            _exporter_attached = True
        except ImportError:
            import warnings
            warnings.warn(
                "OTEL_EXPORTER_OTLP_ENDPOINT is set but "
                "opentelemetry-exporter-otlp-proto-http is not installed. "
                "Traces will not be exported.",
                stacklevel=2,
            )

def get_tracer(name: str = "monet") -> trace.Tracer:
    if _provider is None:
        configure_tracing()
    return trace.get_tracer(name)
```

### 10.2 `docker-compose.dev.yml`

Langfuse is the local development OTLP backend. It is one option, not a requirement.

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: monet
      POSTGRES_USER: monet
      POSTGRES_PASSWORD: monet
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  langfuse:
    image: langfuse/langfuse:latest
    depends_on: [postgres]
    ports:
      - "3000:3000"
    environment:
      DATABASE_URL: postgresql://monet:monet@postgres:5432/monet
      NEXTAUTH_SECRET: dev-secret-change-in-production
      SALT: dev-salt-change-in-production

volumes:
  postgres_data:
```

### 10.3 `.env.example` (committed to repo)

```bash
# Postgres
POSTGRES_URL=postgresql://monet:monet@localhost:5432/monet

# OTel — point at any OTLP-compatible backend
# Langfuse local (default for development):
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:3000/api/public/otel
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic <base64(public_key:secret_key)>
# LangSmith alternative:
# OTEL_EXPORTER_OTLP_ENDPOINT=https://api.smith.langchain.com/otel/v1/traces
# OTEL_EXPORTER_OTLP_HEADERS=x-api-key=<langsmith_api_key>
OTEL_SERVICE_NAME=monet

# Catalogue
MONET_CATALOGUE_DIR=.catalogue
```

---

## 11. Application startup pattern

Document in README. Every monet application follows this:

```python
from monet._catalogue import _set_catalogue_backend
from monet.catalogue import CatalogueService, FilesystemStorage, SQLiteIndex

# 1. Wire catalogue — required for get_catalogue_writer().write()
_set_catalogue_backend(CatalogueService(
    storage=FilesystemStorage(root=".catalogue/artifacts"),
    index=SQLiteIndex(db_url="sqlite+aiosqlite:///.catalogue/index.db"),
))
# OTel reads OTEL_EXPORTER_OTLP_ENDPOINT from environment automatically

# 2. Import agent modules to trigger @agent registration
import my_app.agents  # noqa: F401

# 3. Use orchestration
from monet.orchestration import invoke_agent, run
```

Test fixture pattern:

```python
import pytest
from monet._catalogue import _set_catalogue_backend
from monet.catalogue import InMemoryCatalogueClient
from monet._registry import _default_registry

@pytest.fixture(autouse=True)
def catalogue():
    _set_catalogue_backend(InMemoryCatalogueClient())
    yield
    _set_catalogue_backend(None)

@pytest.fixture
def registry_scope():
    with _default_registry.registry_scope():
        yield
```

---

## 12. Verification checklist

```bash
# 1. Install
uv sync --group dev

# 2. No underscored imports outside src/monet/
grep -r "from monet\._" examples/ tests/ --include="*.py"
# Must return nothing

# 3. Lint
uv run ruff check src/monet/

# 4. Types
uv run mypy src/monet/

# 5. Unit tests — no docker, no API keys
uv run pytest tests/ -m "not integration and not llm_integration" -v

# 6. Public API test — verify everything importable from monet root
# (tests/test_public_api.py should import every __all__ entry and assert
#  it is callable or a type, with no ImportError)

# 7. Catalogue protocol test — verify InMemoryCatalogueClient satisfies protocol
# (tests/test_catalogue.py)

# 8. LangGraph config valid
uv run langgraph dev --help
# Must resolve graph imports without error

# 9. Integration tests — requires docker-compose up
docker compose -f docker-compose.dev.yml up -d
uv run pytest tests/ -m integration -v

# 10. Direct CLI smoke test
uv run python -m monet "Test message"
```

---

## 13. What does not change

These are correct as previously specified. Do not modify:

- Signal model (`list[Signal]`, `emit_signal()`, `has_signal()`, `get_signal()`)
- Decorator behaviour (signal collection, artifact collection via `_artifact_collector`, exception handling)
- `handle_agent_event()` implementation and event vocabulary — moved to `monet.catalogue._events`, behaviour unchanged
- Registry `registry_scope()` for test isolation
- Supervisor graph state schemas and routing logic (from `supervisor-graph.md`)
- `build_entry_graph()`, `build_planning_graph()`, `build_execution_graph()` node implementations
- Kaizen hook
- `emit_progress()` using `get_stream_writer()` with no-op fallback
- `get_run_logger()` exported from `monet.__init__`
- `**kwargs` validation in `invoke_agent()`