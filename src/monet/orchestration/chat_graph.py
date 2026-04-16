"""Chat graph — slash commands + inline triage + direct-LLM respond.

Public shape:

- ``parse_command_node`` — pure-string slash parser, no LLM call.
  Detects ``/plan <task>`` and ``/<agent>:<command> <task>`` and writes
  a routing decision into ``ChatState.route`` + ``ChatState.command_meta``.
- ``triage_node`` — LangChain structured-output classifier that routes
  free-form user text to chat, planner, or specialist. Runs only when
  ``parse_command_node`` didn't already set a route.
- ``respond_node`` — direct LLM call. No ``invoke_agent`` dependency on
  any registered agent. Handles unknown slash commands inline.
- ``planner_node`` / ``specialist_node`` — thin wrappers over
  ``invoke_agent`` that pass the chat message history as context.

Triage is a chat concern, not a pipeline concern. When a user explicitly
types ``/plan`` or ``/<agent>:<cmd>``, the graph skips triage. Free-form
text is the only path that hits the triage LLM.

The graph is returned uncompiled; Aegra / LangGraph Server compiles and
attaches the checkpointer.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from monet.config import ChatConfig
from monet.orchestration._invoke import invoke_agent

PLAN_MAX_REVISIONS = 3


def _artifact_url(artifact_id: str) -> str:
    """Return a clickable URL for an artifact id.

    Uses ``MONET_SERVER_URL`` when set, else defaults to the monet dev
    port. The server exposes artifacts via
    ``GET /api/v1/artifacts/{id}`` so any terminal that auto-linkifies
    URLs can open the full work brief in a browser.
    """
    import os

    from monet._ports import STANDARD_DEV_PORT

    base = (
        os.environ.get("MONET_SERVER_URL", "").rstrip("/")
        or f"http://localhost:{STANDARD_DEV_PORT}"
    )
    return f"{base}/api/v1/artifacts/{artifact_id}/view"


def _message_reducer(
    existing: list[dict[str, Any]] | None,
    new: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Append-only reducer for chat messages."""
    return (existing or []) + new


class ChatState(TypedDict, total=False):
    """State for the chat graph.

    ``messages`` is an append-only transcript. ``route`` and
    ``command_meta`` carry routing decisions written by
    ``parse_command_node`` or ``triage_node``; they guide the
    conditional dispatch to ``respond`` / ``planner`` / ``specialist``.
    ``pending_plan`` is reserved for async plan approval and is unused
    in the current node set — kept in the schema so downstream UIs can
    rely on the field being addressable.
    """

    messages: Annotated[list[dict[str, Any]], _message_reducer]
    route: str | None
    command_meta: dict[str, Any]
    pending_plan: dict[str, Any] | None


class ChatTriageResult(BaseModel):
    """Structured output from the triage classifier.

    ``route`` is the dispatch target. When ``clarification_needed`` is
    True the response node renders ``clarification_prompt`` inline
    instead of routing elsewhere — ambiguous intent should not silently
    escalate into planning.
    """

    route: Literal["chat", "planner", "specialist"]
    specialist: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    clarification_needed: bool = False
    clarification_prompt: str | None = None


# --- Helpers --------------------------------------------------------------


def _last_user_message(messages: list[dict[str, Any]]) -> str:
    """Return the content of the last user message, or empty string."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return str(msg.get("content") or "")
    return ""


def _build_context(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pack the transcript (minus the last user message) as agent context.

    The receiving agent owns any truncation, summarisation, or filtering
    — the graph always forwards the full history so agent-side decisions
    are first-class, not framework-imposed.
    """
    return [
        {
            "type": "chat_history",
            "role": msg.get("role", "user"),
            "content": msg.get("content", ""),
        }
        for msg in messages[:-1]
    ]


def _load_model(model_string: str) -> Any:
    """Return a LangChain chat model for ``model_string`` (``provider:name``)."""
    from langchain.chat_models import init_chat_model  # type: ignore[import-not-found]

    return init_chat_model(model_string)


def _parse_slash(content: str) -> dict[str, Any]:
    """Parse a slash command. Returns a ``{route, command_meta}`` patch.

    Non-slash input returns ``{"route": None}`` to fall through to triage.
    ``/plan <task>`` → planner. ``/<agent>:<cmd> <task>`` → specialist.
    Anything else on the slash prefix is an inline error routed to chat.
    """
    stripped = content.strip()
    if not stripped.startswith("/"):
        return {"route": None}

    head, _, remainder = stripped.partition(" ")
    remainder = remainder.strip()

    if head == "/plan":
        return {
            "route": "planner",
            "command_meta": {"task": remainder},
        }

    token = head.lstrip("/")
    if ":" in token:
        agent_id, _, mode = token.partition(":")
        if agent_id and mode:
            return {
                "route": "specialist",
                "command_meta": {
                    "specialist": agent_id,
                    "mode": mode,
                    "task": remainder,
                },
            }

    return {
        "route": "chat",
        "command_meta": {"unknown_command": head},
    }


def _to_langchain(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Shape monet-style ``{role, content}`` dicts for a LangChain model.

    LangChain's ``init_chat_model(...).ainvoke`` accepts these dicts
    directly; the helper exists so future adjustments (system prompt
    prefix, trimming) have a single touch-point.
    """
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role") or "user"
        content = msg.get("content") or ""
        out.append({"role": role, "content": content})
    return out


def _format_agent_result(result: Any, *, label: str) -> dict[str, str]:
    """Render an :class:`AgentResult` as an assistant chat message."""
    if result is None:
        return {"role": "assistant", "content": f"[{label}] no result."}
    success = getattr(result, "success", True)
    output = getattr(result, "output", None)
    if not success:
        signals = getattr(result, "signals", []) or []
        reason = "; ".join(
            str(s.get("reason") or "").splitlines()[0][:200]
            for s in signals
            if isinstance(s, dict) and s.get("reason")
        )
        content = f"[{label}] failed"
        if reason:
            content += f": {reason}"
        return {"role": "assistant", "content": content}
    if output is None:
        return {"role": "assistant", "content": f"[{label}] complete."}
    if isinstance(output, dict):
        return {"role": "assistant", "content": _summarise_dict_output(label, output)}
    return {"role": "assistant", "content": f"[{label}] {output}"}


def _summarise_dict_output(label: str, output: dict[str, Any]) -> str:
    """Render a structured agent output as a compact, readable summary."""
    skeleton = output.get("routing_skeleton")
    if isinstance(skeleton, dict):
        goal = skeleton.get("goal") or "(no goal)"
        nodes = skeleton.get("nodes")
        n_nodes = len(nodes) if isinstance(nodes, list) else 0
        lines = [f"[{label}] {goal}"]
        lines.append(f"  • {n_nodes} agent step{'s' if n_nodes != 1 else ''}")
        if isinstance(nodes, list):
            for n in nodes[:8]:
                if not isinstance(n, dict):
                    continue
                deps = n.get("depends_on") or []
                dep_str = f" ← {', '.join(deps)}" if deps else ""
                lines.append(
                    f"    - {n.get('id')}: "
                    f"{n.get('agent_id')}/{n.get('command')}{dep_str}"
                )
            if len(nodes) > 8:
                lines.append(f"    … +{len(nodes) - 8} more")
        brief = output.get("work_brief_artifact_id")
        if brief:
            lines.append(f"  • work_brief: {_artifact_url(str(brief))}")
        return "\n".join(lines)

    for key in ("summary", "goal", "task", "verdict", "result", "content"):
        value = output.get(key)
        if isinstance(value, str) and value.strip():
            return f"[{label}] {value.strip()}"

    import json

    try:
        rendered = json.dumps(output, indent=2, default=str)
    except Exception:
        rendered = str(output)
    return f"[{label}]\n{rendered}"


# --- Nodes ----------------------------------------------------------------


async def parse_command_node(state: ChatState) -> dict[str, Any]:
    """Pure-string slash parser. No LLM call."""
    last = _last_user_message(state.get("messages") or [])
    return _parse_slash(last)


async def triage_node(state: ChatState) -> dict[str, Any]:
    """Classify free-form user text into chat / planner / specialist.

    Uses the small/fast model configured in :class:`ChatConfig`. Grounds
    the classifier in the live agent manifest so the ``specialist``
    branch cannot hallucinate agent ids that are not registered. Any
    hallucinated specialist falls back to ``planner`` (the pipeline
    can decompose the task); parse failures fall back to ``chat``.
    """
    cfg = ChatConfig.load()
    messages = state.get("messages") or []
    roster = _known_agent_ids()
    payload = _triage_payload(messages, roster)
    llm = _load_model(cfg.triage_model).with_structured_output(ChatTriageResult)
    try:
        result = await llm.ainvoke(payload)
    except Exception:
        return {"route": "chat", "command_meta": {}}
    if not isinstance(result, ChatTriageResult):
        return {"route": "chat", "command_meta": {}}

    meta: dict[str, Any] = {"task": _last_user_message(messages)}
    if result.clarification_needed and result.clarification_prompt:
        meta["clarification_prompt"] = result.clarification_prompt
        return {"route": "chat", "command_meta": meta}
    if result.route == "specialist":
        chosen = (result.specialist or "").strip()
        if not chosen or (roster and chosen not in roster):
            # Hallucinated or empty — hand off to the planner so the
            # pipeline can decompose the task properly.
            return {"route": "planner", "command_meta": meta}
        meta["specialist"] = chosen
        meta["mode"] = "fast"
    return {"route": result.route, "command_meta": meta}


def _known_agent_ids() -> set[str]:
    """Return the set of agent ids declared in the live manifest."""
    try:
        from monet.core.agent_manifest import get_agent_manifest
    except Exception:
        return set()
    try:
        manifest = get_agent_manifest()
        if not manifest.is_configured():
            return set()
        return {cap["agent_id"] for cap in manifest.list_agents()}
    except Exception:
        return set()


def _triage_payload(
    messages: list[dict[str, Any]],
    roster: set[str],
) -> list[dict[str, Any]]:
    """Prepend a system message listing real agent ids for grounding."""
    base = _to_langchain(messages)
    if not roster:
        system = (
            "You classify chat input. Routes: 'chat' for conversational, "
            "'planner' for multi-step tasks, 'specialist' only when a single "
            "registered agent directly handles the task. No specialist "
            "agents are registered on this server, so never pick 'specialist'."
        )
    else:
        listed = ", ".join(sorted(roster))
        system = (
            "You classify chat input. Routes: 'chat' for conversational, "
            "'planner' for multi-step tasks, 'specialist' only when one of "
            f"these registered agents handles it directly: {listed}. "
            "If the user's request does not obviously match a single agent "
            "from that list, pick 'planner' (never invent an agent id)."
        )
    return [{"role": "system", "content": system}, *base]


async def respond_node(state: ChatState) -> dict[str, Any]:
    """Direct LLM response. No ``invoke_agent``."""
    meta = state.get("command_meta") or {}
    if "unknown_command" in meta:
        cmd = meta["unknown_command"]
        content = (
            f"Unknown command `{cmd}`. Try `/plan <task>` or "
            f"`/<agent>:<command> <task>`."
        )
        return {"messages": [{"role": "assistant", "content": content}]}

    cfg = ChatConfig.load()
    messages = state.get("messages") or []
    payload = _to_langchain(messages)
    clarification = meta.get("clarification_prompt")
    if clarification:
        payload = [
            {"role": "system", "content": str(clarification)},
            *payload,
        ]

    llm = _load_model(cfg.respond_model)
    reply = await llm.ainvoke(payload)
    content = getattr(reply, "content", None) or str(reply)
    return {"messages": [{"role": "assistant", "content": content}]}


async def planner_node(state: ChatState) -> dict[str, Any]:
    """Plan + HITL approval loop.

    Invokes the planner agent, surfaces the plan to the user via a
    form-schema ``interrupt()`` with approve/revise/reject options, and
    loops back into the planner with human feedback on revise. Bounded
    by :data:`PLAN_MAX_REVISIONS` to prevent runaway iterations.
    """
    meta = state.get("command_meta") or {}
    task = str(meta.get("task") or _last_user_message(state.get("messages") or []))
    base_context = _build_context(state.get("messages") or [])
    emitted: list[dict[str, str]] = []
    feedback: str | None = None
    revisions = 0

    while True:
        context = list(base_context)
        if feedback:
            context.append(
                {
                    "type": "instruction",
                    "summary": "Human feedback on the previous plan",
                    "content": feedback,
                }
            )
        try:
            result = await invoke_agent(
                "planner",
                command="plan",
                task=task,
                context=context,
            )
        except Exception as exc:
            emitted.append(
                {"role": "assistant", "content": f"Planner invocation failed: {exc}"}
            )
            return {"messages": emitted}

        plan_message = _format_agent_result(result, label="planner/plan")
        emitted.append(plan_message)

        output = getattr(result, "output", None)
        if not isinstance(output, dict):
            return {"messages": emitted}

        decision = interrupt(_plan_approval_form(output, plan_message["content"]))

        if not isinstance(decision, dict):
            emitted.append({"role": "assistant", "content": "Plan cancelled."})
            return {"messages": emitted}

        action = decision.get("action") or "reject"
        if action == "approve":
            emitted.append({"role": "assistant", "content": "Plan approved."})
            return {"messages": emitted}
        if action == "revise":
            revisions += 1
            new_feedback = str(decision.get("feedback") or "").strip()
            if not new_feedback:
                emitted.append(
                    {
                        "role": "assistant",
                        "content": "Revise requested but no feedback provided; "
                        "treating as rejection.",
                    }
                )
                return {"messages": emitted}
            if revisions > PLAN_MAX_REVISIONS:
                emitted.append(
                    {
                        "role": "assistant",
                        "content": (
                            f"Exceeded {PLAN_MAX_REVISIONS} revisions; stopping."
                        ),
                    }
                )
                return {"messages": emitted}
            feedback = new_feedback
            continue
        # reject or unknown action
        emitted.append({"role": "assistant", "content": "Plan rejected."})
        return {"messages": emitted}


def _plan_approval_form(
    plan_output: dict[str, Any],
    plan_summary: str,
) -> dict[str, Any]:
    """Build the form-schema payload for the plan-approval interrupt."""
    return {
        "prompt": f"{plan_summary}\n\nApprove this plan?",
        "fields": [
            {
                "name": "action",
                "type": "radio",
                "label": "Decision",
                "options": [
                    {"value": "approve", "label": "Approve"},
                    {"value": "revise", "label": "Revise with feedback"},
                    {"value": "reject", "label": "Reject"},
                ],
                "default": "approve",
            },
            {
                "name": "feedback",
                "type": "textarea",
                "label": "Feedback (required for revise)",
                "default": "",
            },
        ],
        "context": {
            "work_brief_artifact_id": plan_output.get("work_brief_artifact_id"),
            "routing_skeleton": plan_output.get("routing_skeleton"),
        },
    }


async def specialist_node(state: ChatState) -> dict[str, Any]:
    """Invoke a named specialist agent parsed from the slash command."""
    meta = state.get("command_meta") or {}
    agent_id = str(meta.get("specialist") or "").strip()
    if not agent_id:
        return {
            "messages": [
                {
                    "role": "assistant",
                    "content": "No specialist name provided.",
                }
            ]
        }
    mode = str(meta.get("mode") or "fast")
    task = str(meta.get("task") or _last_user_message(state.get("messages") or []))
    messages = state.get("messages") or []
    try:
        result = await invoke_agent(
            agent_id,
            command=mode,
            task=task,
            context=_build_context(messages),
        )
    except Exception as exc:
        return {
            "messages": [
                {
                    "role": "assistant",
                    "content": f"Agent `{agent_id}/{mode}` unavailable: {exc}",
                }
            ]
        }
    return {
        "messages": [_format_agent_result(result, label=f"{agent_id}/{mode}")],
    }


# --- Edge routers ---------------------------------------------------------


def _route_after_parse(state: ChatState) -> str:
    route = state.get("route")
    if route is None:
        return "triage"
    if route == "planner":
        return "planner"
    if route == "specialist":
        return "specialist"
    return "respond"


def _route_after_triage(state: ChatState) -> str:
    route = state.get("route")
    if route == "planner":
        return "planner"
    if route == "specialist":
        return "specialist"
    return "respond"


# --- Graph builder --------------------------------------------------------


def build_chat_graph() -> StateGraph[ChatState]:
    """Build the chat graph. Returns uncompiled ``StateGraph[ChatState]``.

    Aegra / LangGraph Server compiles it and attaches the checkpointer.
    """
    graph: StateGraph[ChatState] = StateGraph(ChatState)
    graph.add_node("parse", parse_command_node)
    graph.add_node("triage", triage_node)
    graph.add_node("respond", respond_node)
    graph.add_node("planner", planner_node)
    graph.add_node("specialist", specialist_node)

    graph.add_edge(START, "parse")
    graph.add_conditional_edges(
        "parse",
        _route_after_parse,
        {
            "triage": "triage",
            "respond": "respond",
            "planner": "planner",
            "specialist": "specialist",
        },
    )
    graph.add_conditional_edges(
        "triage",
        _route_after_triage,
        {
            "respond": "respond",
            "planner": "planner",
            "specialist": "specialist",
        },
    )
    graph.add_edge("respond", END)
    graph.add_edge("planner", END)
    graph.add_edge("specialist", END)
    return graph
