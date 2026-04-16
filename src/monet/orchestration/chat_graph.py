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
from monet.signals import SignalType

PLAN_MAX_REVISIONS = 3
#: Ceiling on follow-up question rounds the planner may request per turn.
#: On the (MAX+1)th invocation the planner is instructed to produce a
#: best-effort plan regardless of remaining ambiguity — we'd rather ship
#: an imperfect plan the user can reject than loop forever on questions.
MAX_FOLLOWUP_ATTEMPTS = 1


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

    Planner follow-up state (all first-class so durable execution
    survives worker restart without losing loop counters):

    - ``followup_attempts``: number of times planner has asked questions.
    - ``followup_answers``: most recent human answers, injected into
      context on the next planner invocation.
    - ``pending_questions``: questions the planner emitted; consumed by
      ``questionnaire_node``.
    - ``plan_revisions``: count of revise cycles after a plan was shown.
    - ``plan_feedback``: latest revise feedback, injected into context
      on the next planner invocation.
    - ``last_plan_output``: planner's most recent plan dict; consumed
      by ``approval_node``.
    """

    messages: Annotated[list[dict[str, Any]], _message_reducer]
    route: str | None
    command_meta: dict[str, Any]
    followup_attempts: int
    followup_answers: list[dict[str, Any]] | None
    pending_questions: list[str] | None
    plan_revisions: int
    plan_feedback: str | None
    last_plan_output: dict[str, Any] | None


class ChatTriageResult(BaseModel):
    """Structured output from the triage classifier — information only.

    The classifier answers **"is this conversational or does it need a
    plan?"**. It does not pick a specific agent or command — that's the
    planner's job. Keeping the decision surface small here (chat vs
    plan) leaves agent-selection where it belongs and avoids
    hallucinated specialist routing.

    ``clarification_needed`` allows the classifier to refuse both
    routes when the user's intent is genuinely ambiguous — the response
    node renders ``clarification_prompt`` inline so the user can restate
    before either path fires.
    """

    route: Literal["chat", "plan"]
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
    """Render an :class:`AgentResult` as an assistant chat message.

    Always appends artifact URLs when ``result.artifacts`` is non-empty
    so agents that write outputs to the store (researcher, writer, qa)
    surface a clickable link, not just the inline preview.
    """
    if result is None:
        return {"role": "assistant", "content": f"[{label}] no result."}
    success = getattr(result, "success", True)
    output = getattr(result, "output", None)
    artifacts = getattr(result, "artifacts", ()) or ()
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
        return {
            "role": "assistant",
            "content": _append_artifact_links(content, artifacts),
        }
    if output is None:
        body = f"[{label}] complete."
    elif isinstance(output, dict):
        body = _summarise_dict_output(label, output)
    else:
        body = f"[{label}] {output}"
    return {
        "role": "assistant",
        "content": _append_artifact_links(body, artifacts),
    }


def _append_artifact_links(content: str, artifacts: Any) -> str:
    """Append ``→ <url>`` lines for every artifact with an ``artifact_id``."""
    links: list[str] = []
    for artifact in artifacts or ():
        if not isinstance(artifact, dict):
            continue
        artifact_id = str(artifact.get("artifact_id") or "").strip()
        if not artifact_id:
            continue
        key = str(artifact.get("key") or "").strip()
        label = f" ({key})" if key else ""
        links.append(f"→ artifact{label}: {_artifact_url(artifact_id)}")
    if not links:
        return content
    return content + "\n\n" + "\n".join(links)


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
    """Classify free-form user text as ``chat`` or ``plan``.

    The classifier is a small/fast model that returns *information*
    (route + confidence + optional clarification prompt) — it does not
    pick an agent. Agent selection is the planner's job. Parse
    failures fall back to ``chat`` (a direct-LLM response is cheaper
    than an unnecessary plan).
    """
    cfg = ChatConfig.load()
    messages = state.get("messages") or []
    payload = _triage_payload(messages)
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
    # Map the "plan" classification to the "planner" edge name.
    node_route = "planner" if result.route == "plan" else "chat"
    return {"route": node_route, "command_meta": meta}


def _triage_payload(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Prepend a system message explaining the chat-vs-plan decision."""
    base = _to_langchain(messages)
    system = (
        "You classify the latest user message. Return one of two routes:\n"
        "- 'chat': conversational, informational, or a question answerable "
        "directly in natural language without tools or multi-step work.\n"
        "- 'plan': the user wants something done that requires research, "
        "generation, analysis, tool use, or multiple agent steps — "
        "anything beyond a plain reply.\n\n"
        "Do NOT pick a specific agent or command; that is the planner's "
        "job. If the user's intent is genuinely ambiguous, set "
        "clarification_needed and include a clarification_prompt asking "
        "them to restate. Bias toward 'plan' when unsure between plan "
        "and chat — producing an unnecessary plan (the user can reject "
        "it) is cheaper than silently downgrading a real task to a chat "
        "reply."
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
    """Invoke the planner agent. One LLM call per visit, no inner loop.

    Consumes ``plan_feedback`` (from a prior revise) and
    ``followup_answers`` (from the questionnaire), injects them as
    context, then invokes ``planner:plan``. The result is either:

    - A plan — stored in ``last_plan_output``, routed to
      :func:`approval_node`.
    - Follow-up questions (signal ``NEEDS_CLARIFICATION``) — stored in
      ``pending_questions``, routed to :func:`questionnaire_node`.
    - Questions emitted on the forced pass (``followup_attempts >=
      MAX_FOLLOWUP_ATTEMPTS``) — treated as give-up; planner_node
      emits an apology message and terminates.

    All inputs are read from state so LangGraph's durable execution can
    resume this node from a crash without losing loop counters or
    pending plan data.
    """
    meta = state.get("command_meta") or {}
    task = str(meta.get("task") or _last_user_message(state.get("messages") or []))
    attempts = state.get("followup_attempts") or 0
    force_plan = attempts >= MAX_FOLLOWUP_ATTEMPTS
    context = _build_planner_context(state, force_plan=force_plan)

    try:
        result = await invoke_agent(
            "planner",
            command="plan",
            task=task,
            context=context,
        )
    except Exception as exc:
        return {
            "messages": [
                {"role": "assistant", "content": f"Planner invocation failed: {exc}"}
            ],
            "last_plan_output": None,
            "pending_questions": None,
            "plan_feedback": None,
            "followup_answers": None,
        }

    output = getattr(result, "output", None)
    signals = list(getattr(result, "signals", ()) or ())
    questions_signalled = any(
        s.get("type") == SignalType.NEEDS_CLARIFICATION
        for s in signals
        if isinstance(s, dict)
    )
    is_questions = isinstance(output, dict) and (
        output.get("kind") == "questions" or questions_signalled
    )

    # Clear consumed inputs regardless of path — they've been passed
    # into context and shouldn't re-apply on the next visit.
    cleared: dict[str, Any] = {"plan_feedback": None, "followup_answers": None}

    if is_questions:
        questions = []
        if isinstance(output, dict):
            raw = output.get("questions") or []
            questions = [str(q).strip() for q in raw if str(q).strip()]
        if force_plan or not questions:
            # Planner asked despite the force-plan instruction (or
            # returned no usable questions). Give up gracefully.
            plan_message = _format_agent_result(result, label="planner/plan")
            return {
                "messages": [
                    plan_message,
                    {
                        "role": "assistant",
                        "content": (
                            "Couldn't produce a plan without more specifics. "
                            "Please restate the task with the details you have."
                        ),
                    },
                ],
                "last_plan_output": None,
                "pending_questions": None,
                **cleared,
            }
        return {
            "pending_questions": questions,
            "last_plan_output": None,
            **cleared,
        }

    # Plan path (explicit kind="plan" or legacy output without kind).
    if not isinstance(output, dict):
        # Unknown output shape — surface raw message and stop.
        plan_message = _format_agent_result(result, label="planner/plan")
        return {
            "messages": [plan_message],
            "last_plan_output": None,
            "pending_questions": None,
            **cleared,
        }
    return {
        "last_plan_output": output,
        "pending_questions": None,
        **cleared,
    }


def _build_planner_context(
    state: ChatState, *, force_plan: bool
) -> list[dict[str, Any]]:
    """Assemble planner context: transcript + feedback + answers + force flag.

    Kept separate from :func:`planner_node` so the context shape is
    testable in isolation — the prompt is the real contract with the
    agent, and its context block is the richest place for bugs to hide.
    """
    messages = state.get("messages") or []
    context: list[dict[str, Any]] = list(_build_context(messages))

    feedback = state.get("plan_feedback")
    if feedback:
        context.append(
            {
                "type": "instruction",
                "summary": "Human feedback on the previous plan",
                "content": feedback,
            }
        )

    for answer in state.get("followup_answers") or []:
        context.append(answer)

    if force_plan:
        context.append(
            {
                "type": "instruction",
                "summary": "Force-plan override",
                "content": (
                    "Produce a best-effort plan now — do NOT return more "
                    "questions. The user will review and can reject or "
                    "revise. If any parameter is still unknown, pick a "
                    "reasonable default and note it in `assumptions`."
                ),
            }
        )
    return context


async def questionnaire_node(state: ChatState) -> dict[str, Any]:
    """Render pending planner questions as a form, interrupt, collect answers.

    Uses the ``select_or_text`` field type so each question gets a
    pre-filled "I don't know" / "skip" option plus free-form text —
    matches the claude-code-style questionnaire idiom. Follow-up
    attempts are bumped in state so the planner can detect the
    force-plan condition on the next visit.
    """
    questions = list(state.get("pending_questions") or [])
    attempts = state.get("followup_attempts") or 0
    if not questions:
        # Defensive — routed here without questions. Clear flags and
        # loop back to planner (which will produce a plan).
        return {"pending_questions": None, "followup_attempts": attempts}

    form = _followup_form(questions)
    decision = interrupt(form)

    answers: list[dict[str, Any]] = []
    if isinstance(decision, dict):
        for idx, question in enumerate(questions):
            raw = decision.get(f"q{idx}")
            if raw in (None, "", "__skip__"):
                continue
            answers.append(
                {
                    "type": "user_clarification",
                    "summary": question,
                    "content": f"Q: {question}\nA: {raw}",
                }
            )

    return {
        "pending_questions": None,
        "followup_attempts": attempts + 1,
        "followup_answers": answers,
    }


def _followup_form(questions: list[str]) -> dict[str, Any]:
    """Form-schema payload for the planner's follow-up questionnaire."""
    fields: list[dict[str, Any]] = []
    for idx, question in enumerate(questions):
        fields.append(
            {
                "name": f"q{idx}",
                "type": "select_or_text",
                "label": question,
                "options": [
                    {"value": "__skip__", "label": "Skip / I don't know"},
                ],
                "default": "__skip__",
            }
        )
    return {
        "prompt": (
            "The planner needs more information before it can build a plan. "
            "Answer each question, or pick 'Skip' if you don't know — the "
            "planner will use its best judgement for skipped items."
        ),
        "render": "inline",
        "fields": fields,
    }


async def approval_node(state: ChatState) -> dict[str, Any]:
    """Show the plan to the user, interrupt for approve/revise/reject.

    Revision counter lives in state (``plan_revisions``) so LangGraph
    replay after a crash sees the same value and doesn't re-offer
    already-consumed revise slots. On exceeded revisions or reject the
    node terminates; on revise with feedback it clears the plan and
    routes back to :func:`planner_node` for another attempt.
    """
    plan_output = state.get("last_plan_output") or {}
    revisions = state.get("plan_revisions") or 0

    plan_message_text = _summarise_dict_output("planner/plan", plan_output)
    form = _plan_approval_form(plan_output, plan_message_text)
    decision = interrupt(form)

    plan_message = {"role": "assistant", "content": plan_message_text}

    if not isinstance(decision, dict):
        return {
            "messages": [
                plan_message,
                {"role": "assistant", "content": "Plan cancelled."},
            ],
            "last_plan_output": None,
        }

    action = decision.get("action") or "reject"
    if action == "approve":
        approved = {"role": "assistant", "content": "Plan approved."}
        return {
            "messages": [plan_message, approved],
            "last_plan_output": None,
        }
    if action == "revise":
        feedback = str(decision.get("feedback") or "").strip()
        if not feedback:
            return {
                "messages": [
                    plan_message,
                    {
                        "role": "assistant",
                        "content": "Revise requested but no feedback provided; "
                        "treating as rejection.",
                    },
                ],
                "last_plan_output": None,
            }
        if revisions + 1 > PLAN_MAX_REVISIONS:
            return {
                "messages": [
                    plan_message,
                    {
                        "role": "assistant",
                        "content": (
                            f"Exceeded {PLAN_MAX_REVISIONS} revisions; stopping."
                        ),
                    },
                ],
                "last_plan_output": None,
            }
        return {
            "messages": [plan_message],
            "plan_feedback": feedback,
            "plan_revisions": revisions + 1,
            "last_plan_output": None,
        }
    # reject or unknown action
    return {
        "messages": [plan_message, {"role": "assistant", "content": "Plan rejected."}],
        "last_plan_output": None,
    }


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
    return "respond"


def _route_after_planner(state: ChatState) -> str:
    """Three-way fork: questionnaire, approval, or terminal message.

    ``pending_questions`` and ``last_plan_output`` are mutually
    exclusive outputs of :func:`planner_node` — this router just picks
    which follow-up node to visit. When planner_node emits an apology
    (both fields cleared, messages written) we route to END.
    """
    if state.get("pending_questions"):
        return "questionnaire"
    if state.get("last_plan_output"):
        return "approval"
    return "__end__"


def _route_after_approval(state: ChatState) -> str:
    """Route revise back into planner; approve/reject terminate.

    Approval writes ``plan_feedback`` only on revise; its presence is
    the signal to re-enter the planner loop.
    """
    if state.get("plan_feedback"):
        return "planner"
    return "__end__"


# --- Graph builder --------------------------------------------------------


def build_chat_graph() -> StateGraph[ChatState]:
    """Build the chat graph. Returns uncompiled ``StateGraph[ChatState]``.

    Aegra / LangGraph Server compiles it and attaches the checkpointer.

    The planner flow is a three-node state machine — ``planner``
    (invoke agent), ``questionnaire`` (clarify with the human),
    ``approval`` (HITL plan review). Conditional edges route on
    first-class ChatState fields so every loop counter is durable.
    """
    graph: StateGraph[ChatState] = StateGraph(ChatState)
    graph.add_node("parse", parse_command_node)
    graph.add_node("triage", triage_node)
    graph.add_node("respond", respond_node)
    graph.add_node("planner", planner_node)
    graph.add_node("questionnaire", questionnaire_node)
    graph.add_node("approval", approval_node)
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
        },
    )
    graph.add_conditional_edges(
        "planner",
        _route_after_planner,
        {
            "questionnaire": "questionnaire",
            "approval": "approval",
            "__end__": END,
        },
    )
    graph.add_edge("questionnaire", "planner")
    graph.add_conditional_edges(
        "approval",
        _route_after_approval,
        {
            "planner": "planner",
            "__end__": END,
        },
    )
    graph.add_edge("respond", END)
    graph.add_edge("specialist", END)
    return graph
