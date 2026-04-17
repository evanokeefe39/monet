"""Subprocess sandbox for candidate agents — dev-only, swap in production.

**Not a security boundary.** Candidate code runs in the worker process's
own Python interpreter, isolated only to a temporary working directory.
A malicious candidate can read env vars, write outside the tmp dir, call
the network, and exhaust host memory / CPU. Do not use this on untrusted
candidates in a shared environment.

**Production path — replace this helper** with a managed code-execution
service that gives candidates CPU, memory, filesystem, and network
isolation. Proven options:

- **Modal** (``modal.Sandbox``) — ephemeral containers, per-call billing,
  clone-and-run from a Git URL, ship signed requirements. Best fit when
  the candidate pipeline itself lives in Python and you want a drop-in
  replacement for this subprocess call. See
  https://modal.com/docs/guide/sandbox.
- **E2B Sandboxes** — Firecracker microVMs via HTTP, good Python SDK.
- **Google Cloud Run Jobs** / **AWS Fargate Task** / **Azure Container
  Apps Jobs** — when you already have cloud credentials and want
  control-plane access to an existing VPC / secrets manager.
- **Kubernetes Job with a strict PodSecurityStandard + resource quota** —
  when the fleet is in-house.

Shape the replacement to match this module's public function: accept a
candidate + fixture, return an :class:`ExecutionReport`. The
``code_executor`` agent stays unchanged.

Tracked as a follow-on under the roadmap "Sandbox integration" item; the
subprocess helper ships only to make the reference pipeline runnable
without a cloud account.

The sandbox collects structured events from the candidate's stdout via
``AgentStream.cli()``. Candidates that emit monet-style event JSON
(``{type: "progress" | "artifact" | "signal" | "result" | "error", ...}``)
have their events surfaced to the caller; plain stdout is captured as
``AgentStreamEvent`` entries with ``type="log"``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .schemas import ExecutionReport


async def run_candidate_in_subprocess(
    *,
    candidate_id: str,
    source_code: str,
    entrypoint: str,
    fixture: dict[str, Any],
    assertions: list[dict[str, Any]],
    timeout_s: float,
) -> ExecutionReport:
    """Run one candidate against one fixture. Returns an ExecutionReport.

    The candidate's source is written to ``{tmp}/agent.py``; the fixture
    is written to ``{tmp}/task.json``. The process is launched with
    ``{tmp}`` as the working directory and given ``timeout_s`` seconds
    to finish. Timeout, non-zero exit, or malformed output all surface
    as an unsuccessful report — never as exceptions — so the
    caller can aggregate a mixed-outcome list cleanly.
    """
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / entrypoint).write_text(source_code, encoding="utf-8")
        (tmp_path / "task.json").write_text(json.dumps(fixture), encoding="utf-8")

        proc = await asyncio.create_subprocess_exec(
            "python",
            "-u",
            entrypoint,
            cwd=str(tmp_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        start = time.perf_counter()
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s
            )
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            duration_ms = (time.perf_counter() - start) * 1000.0
            return ExecutionReport(
                candidate_id=candidate_id,
                ok=False,
                stderr=f"timed out after {timeout_s}s",
                exit_code=-1,
                duration_ms=duration_ms,
                assertion_pass_rate=0.0,
                events=[{"type": "error", "reason": "timeout"}],
            )

        duration_ms = (time.perf_counter() - start) * 1000.0
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        exit_code = proc.returncode or 0
        events = _parse_events(stdout)
        assertion_pass_rate = _score_assertions(assertions, stdout, exit_code)
        ok = exit_code == 0 and assertion_pass_rate == 1.0

        return ExecutionReport(
            candidate_id=candidate_id,
            ok=ok,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_ms=duration_ms,
            assertion_pass_rate=assertion_pass_rate,
            events=events,
        )


def _parse_events(stdout: str) -> list[dict[str, Any]]:
    """Best-effort parse of monet-style event lines from stdout.

    Lines that start with ``{`` and parse as JSON become event dicts;
    everything else becomes a ``{type: "log", line: ...}`` entry. Mirrors
    the ``AgentStream.cli()`` contract: a candidate that only prints plain
    text still shows up as structured ``log`` events.
    """
    events: list[dict[str, Any]] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                events.append({"type": "log", "line": line})
                continue
            if isinstance(parsed, dict):
                events.append(parsed)
                continue
        events.append({"type": "log", "line": line})
    return events


def _score_assertions(
    assertions: list[dict[str, Any]], stdout: str, exit_code: int
) -> float:
    """Run declarative assertions over stdout and exit_code.

    Supported ops: ``stdout_contains``, ``stdout_not_contains``,
    ``exit_code``. Unknown ops fail the assertion — fail-closed beats
    silently passing an assertion the harness did not understand.
    """
    if not assertions:
        return 1.0
    passed = 0
    for assertion in assertions:
        op = assertion.get("op")
        value = assertion.get("value")
        if op == "stdout_contains":
            if isinstance(value, str) and value in stdout:
                passed += 1
        elif op == "stdout_not_contains":
            if isinstance(value, str) and value not in stdout:
                passed += 1
        elif op == "exit_code" and exit_code == value:
            passed += 1
    return passed / len(assertions)
