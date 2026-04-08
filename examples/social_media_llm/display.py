"""Print helpers + artifact-aware wave-result renderer.

The wave-result renderer is the litmus test for the v3 SDK refactor: it
walks ``wave_result["artifacts"]`` directly and resolves each
``ArtifactPointer`` via ``catalogue.read``. No regex, no parsing of
``wave_result["output"]`` strings, no ``ArtifactPointer.repr()`` hacks —
because the SDK now exposes ``output`` and ``artifacts`` as distinct
fields on ``WaveResult``.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from monet._catalogue import CatalogueHandle


# ── Headers / sections ────────────────────────────────────────────────


def print_header(text: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {text}")
    print(f"{'=' * 60}\n")


def print_env_status(missing: list[str]) -> None:
    """Print which API keys are configured. ``missing`` is the result of
    ``app.check_environment()``."""
    keys = {
        "GEMINI_API_KEY": "Gemini (planner, researcher, writer, publisher)",
        "GROQ_API_KEY": "Groq (QA)",
        "TAVILY_API_KEY": "Tavily (web search)",
    }
    print("  Configured providers:")
    for key, desc in keys.items():
        status = "MISSING" if key in missing else "ok"
        print(f"    {desc}: {status}")
    print()


# ── Phase output ──────────────────────────────────────────────────────


def print_triage(triage: dict[str, Any]) -> None:
    complexity = triage.get("complexity", "?")
    print(f"\n  Triage result: complexity={complexity}")
    suggested = triage.get("suggested_agents", []) or []
    if suggested:
        print(f"  Suggested agents: {', '.join(suggested)}")


def print_brief(brief: dict[str, Any]) -> None:
    print("\n  --- Work Brief ---")
    print(f"  Goal: {brief.get('goal', 'N/A')}")
    in_scope = brief.get("in_scope", []) or []
    out_scope = brief.get("out_of_scope", []) or []
    print(f"  In scope: {', '.join(in_scope)}")
    print(f"  Out of scope: {', '.join(out_scope)}")
    phases = brief.get("phases", []) or []
    print(f"  Phases ({len(phases)}):")
    for i, phase in enumerate(phases):
        waves = phase.get("waves", []) or []
        item_count = sum(len(w.get("items", []) or []) for w in waves)
        name = phase.get("name", "?")
        print(f"    {i + 1}. {name} ({len(waves)} waves, {item_count} items)")
    assumptions = brief.get("assumptions", []) or []
    if assumptions:
        print(f"  Assumptions: {', '.join(assumptions)}")
    print("  --- End Brief ---")


def print_streaming_event(label: str, mode: str, data: Any) -> None:
    """Render one streamed (mode, data) tuple from the SDK client.

    ``label`` is the phase name (``"triage"`` / ``"planning"`` /
    ``"execution"``) used for the bracket prefix on update events.
    """
    if mode == "custom":
        if not isinstance(data, dict):
            return
        status = data.get("status", "")
        agent_name = data.get("agent", "")
        # Andon cord: agent_node emits this on failure so the operator
        # sees the upstream failure inline with the run log rather than
        # having to correlate an empty wave_result to a QA "no content".
        if status == "agent failed":
            reasons = data.get("reasons") or ""
            types = data.get("signal_types") or []
            header = f"!! {agent_name}/{data.get('command', '')} FAILED"
            if types:
                header += f" [{', '.join(t for t in types if t)}]"
            print(header)
            if reasons:
                print(f"   reason: {reasons}")
            return
        if agent_name:
            print(f"    -> {agent_name}: {status}")
        elif status:
            print(f"    -> {status}")
    elif mode == "updates":
        if not isinstance(data, dict):
            return
        for node_name in data:
            if node_name in ("__start__", "__interrupt__"):
                continue
            print(f"  [{label}] {node_name} complete")


# ── Wave-result renderer (the regex-killer) ───────────────────────────


async def print_wave_results(
    results: list[dict[str, Any]],
    catalogue: CatalogueHandle,
) -> None:
    """Render every wave_result with its artifact bodies inlined.

    For each result, prefer ``wr["artifacts"]`` and resolve each pointer
    against the local catalogue. Fall back to ``wr["output"]`` only when
    no artifacts are attached.
    """
    print("\n  Wave results:")
    for wr in results:
        pi = wr.get("phase_index")
        wi = wr.get("wave_index")
        ii = wr.get("item_index")
        aid = wr.get("agent_id")
        cmd = wr.get("command")
        print(f"\n    [{pi}.{wi}.{ii}] {aid}/{cmd}:")

        # Andon cord: failures take over the block. Any signal whose
        # type looks like an error is surfaced loudly; the artifact /
        # output rendering is skipped because empty content on a
        # failed agent is noise, not data.
        signals = wr.get("signals") or []
        failure_signals = [
            s for s in signals if _is_failure_signal(s.get("type") or "")
        ]
        if failure_signals:
            for s in failure_signals:
                reason_line = (s.get("reason") or "").splitlines()[0][:300]
                _safe_print(f"    !! {s.get('type')}: {reason_line}")
                meta = s.get("metadata") or {}
                if meta.get("error_type"):
                    _safe_print(f"       error_type={meta['error_type']}")
            continue

        artifacts = wr.get("artifacts") or []
        if artifacts:
            for pointer in artifacts:
                artifact_id = pointer.get("artifact_id")
                if not artifact_id:
                    continue
                try:
                    content, meta = await catalogue.read(artifact_id)
                except (KeyError, ValueError, FileNotFoundError) as e:
                    print(f"    (could not read artifact {artifact_id[:8]}...: {e})")
                    continue
                url = pointer.get("url") or ""
                label = (
                    f"open artifact {artifact_id[:8]}"
                    f" ({meta['content_type']}, {meta['content_length']}b)"
                )
                if url and _supports_osc8():
                    link = _osc8(url, label)
                elif url:
                    link = f"{label} — {url}"
                else:
                    link = label
                _safe_print(f"      {link}")
                preview = _preview(content)
                if preview:
                    _safe_print(f"      > {preview}")
            continue

        output = wr.get("output")
        if output is not None:
            _safe_print(f"    {str(output)[:200]}")


def print_reflections(reflections: list[dict[str, Any]]) -> None:
    if not reflections:
        return
    print("\n  QA reflections:")
    for ref in reflections:
        line = (
            f"    Phase {ref.get('phase_index')}, "
            f"Wave {ref.get('wave_index')}: "
            f"{ref.get('verdict')} -- {ref.get('notes', '')}"
        )
        _safe_print(line)


def print_summary(
    run_id: str,
    work_brief: dict[str, Any],
    final_state: dict[str, Any],
) -> None:
    completed = final_state.get("completed_phases", []) or []
    wave_results = final_state.get("wave_results", []) or []
    reflections = final_state.get("wave_reflections", []) or []
    failed = sum(
        1
        for wr in wave_results
        if any(
            _is_failure_signal(s.get("type") or "") for s in (wr.get("signals") or [])
        )
    )
    print(f"  Run ID: {run_id}")
    print(
        f"  Phases completed: {len(completed)}/"
        f"{len(work_brief.get('phases', []) or [])}"
    )
    print(f"  Total agent invocations: {len(wave_results)}")
    if failed:
        print(f"  Failed invocations: {failed}")
    print(f"  QA reflections: {len(reflections)}")
    if final_state.get("abort_reason"):
        print(f"  Aborted: {final_state['abort_reason']}")


# ── Internals ─────────────────────────────────────────────────────────


_FAILURE_SIGNAL_TYPES: frozenset[str] = frozenset(
    {
        "semantic_error",
        "needs_human_review",
        "escalation_required",
    }
)


def _is_failure_signal(signal_type: str) -> bool:
    """True for signal types that represent an upstream failure worth
    surfacing prominently to the operator. Kept narrow on purpose: a
    routing or audit signal is not an error."""
    if signal_type in _FAILURE_SIGNAL_TYPES:
        return True
    return signal_type.endswith("_error")


def _osc8(url: str, text: str) -> str:
    """Emit an OSC 8 hyperlink escape sequence. Windows Terminal, iTerm2,
    GNOME Terminal, and most modern emulators render this as a clickable
    link; unsupported terminals show ``text`` with a few stray escape
    characters, which is why ``_supports_osc8`` gates its use."""
    return f"\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\"


def _supports_osc8() -> bool:
    """Heuristic: only emit OSC 8 when stdout is a real TTY and TERM is
    not the sentinel ``dumb``. Piping to a file falls back to plain URLs."""
    if not sys.stdout.isatty():
        return False
    return os.environ.get("TERM") != "dumb"


def _preview(content: bytes, max_chars: int = 200) -> str:
    """First non-empty line of ``content``, trimmed to ``max_chars``.

    Markdown bodies from the reference writer agent lead with a title
    line; showing that plus a character ellipsis gives enough signal to
    decide whether to click through to the full artifact."""
    text = content.decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    first = text.split("\n", 1)[0].strip()
    if len(first) > max_chars:
        return first[: max_chars - 1] + "…"
    return first


def _safe_print(line: str) -> None:
    """``print`` that survives Windows cp1252 consoles by replacing chars
    the active stdout encoding can't render."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode(encoding, errors="replace").decode(encoding))
