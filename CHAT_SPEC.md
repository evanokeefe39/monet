# Spec: Chat Graph + CLI Integration

## Purpose

Refactor the chat graph and CLI input loop to support:
- Slash command parsing with dynamic registry-driven completions
- Inline triage within the chat graph (replacing the standalone triage graph)
- Two swappable chat graph implementations (default and agentic)
- Clean routing to planner and specialist graphs via conditional edges

This spec is intended for integration with the existing codebase. Do not create new abstractions where existing ones already serve the purpose. Prefer small, targeted changes over rewrites.

---

## Context and Tech Stack

- LangGraph for graph construction and state management
- Aegra for thread management and checkpointing — graphs are returned uncompiled
- PydanticAI for structured LLM output in the default chat implementation
- `textual` for the CLI REPL — chat interface, slash command completions, and interrupt form rendering
- Agents register themselves from worker processes in a separate execution environment to the orchestration server
- The slash command registry is populated from registered agents at runtime — it is not hardcoded

---

## Current State

- `build_chat_graph()` exists in `monet/graphs/chat.py` — single node, calls `invoke_agent("planner", command="chat")` which does not exist and should be removed
- A triage graph exists separately — its location and current interface need to be confirmed before integration
- The CLI input loop location and current implementation need to be confirmed
- `invoke_agent` exists in `monet/orchestration/_invoke.py`

Before making any changes, read the existing files listed above and confirm their structure with the developer.

---

## Graph Architecture

### State

Extend `ChatState` to carry routing signals set by the parse and triage nodes:

```python
class ChatState(TypedDict, total=False):
    messages: Annotated[list[dict[str, Any]], _message_reducer]
    route: str | None                    # "chat" | "planner" | "specialist"
    command_meta: dict[str, Any]         # specialist name, mode, task remainder
    pending_plan: dict[str, Any] | None  # reserved for async plan approval
```

### Graph Shape

```
entry
  └── parse_command_node
        ├── slash command detected → set route + command_meta → respond_node (skip triage)
        └── no slash command → triage_node
                                  ├── "chat"       → respond_node → END
                                  ├── "planner"    → planner_node → END
                                  └── "specialist" → specialist_node → END
```

Conditional edges read `state["route"]` to dispatch. `parse_command_node` sets route directly for slash commands, bypassing triage entirely.

### Nodes

**`parse_command_node`**

Pure string logic, no LLM call. Inspects the last user message.

- If content does not start with `/`: return `{"route": None}` to fall through to triage
- `/plan <message>`: `{"route": "planner", "command_meta": {"task": remainder}}`
- `/<specialist>:<mode> <message>`: `{"route": "specialist", "command_meta": {"specialist": ..., "mode": ..., "task": remainder}}`
- Unknown command: `{"route": "chat", "command_meta": {"unknown_command": command}}` — chat node handles the error response inline

**`triage_node`**

Single fast LLM call via PydanticAI. Low token budget. Returns structured output:

```python
class TriageResult(BaseModel):
    route: Literal["chat", "planner", "specialist"]
    specialist: str | None = None
    confidence: float  # 0.0–1.0
    clarification_needed: bool = False
```

If `clarification_needed` is True, route to `respond_node` with a clarifying question rather than escalating with unclear intent. The clarification response should be generated inline — do not escalate ambiguous intent to the planner.

**`respond_node`** (default implementation)

Direct LLM call. No `invoke_agent`. No dependency on any registered agent.

```python
async def respond_node(state: ChatState) -> dict[str, Any]:
    messages = state.get("messages") or []
    llm = get_llm()
    response = await llm.ainvoke(messages)
    return {"messages": [{"role": "assistant", "content": response.content}]}
```

If `command_meta` contains `unknown_command`, prepend an error message to the response rather than calling the LLM.

**`planner_node`**

Calls `invoke_agent("planner", ...)`. Passes full message history as context plus `command_meta["task"]` as the task. The planner agent decides what to do with the context.

```python
context = _build_context(messages)
result = await invoke_agent("planner", task=task, context=context)
```

**`specialist_node`**

Calls `invoke_agent(command_meta["specialist"], ...)`. Passes full message history as context, `command_meta["mode"]` if present, and `command_meta["task"]` as the task.

**`_build_context` helper**

Shared across planner and specialist nodes. Agents are responsible for filtering, truncating, or embedding this as they see fit — the framework always passes the full history.

```python
def _build_context(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "chat_history",
            "role": msg.get("role", "user"),
            "content": msg.get("content", ""),
        }
        for msg in messages[:-1]
    ]
```

Note: remove the 500-character truncation that exists in the current implementation. Truncation is the agent's responsibility.

---

## Two Chat Graph Implementations

The framework ships two implementations. Which is used is determined by a config key resolved at startup.

### Config

```yaml
chat:
  graph: "monet.graphs.chat:build_chat_graph"          # default
  # graph: "myapp.graphs.agent_chat:build_chat_graph"  # user override
```

The config loader resolves the dotted path and calls the factory function. The returned `StateGraph` is compiled and checkpointed by Aegra as normal. Both implementations must accept no arguments and return an uncompiled `StateGraph[ChatState]`.

### Default (`monet/graphs/chat.py`)

Runs in orchestration. Direct LLM call in `respond_node`. No `invoke_agent` dependency for conversational turns. Triage via PydanticAI structured output.

### Agentic (`monet/graphs/chat_agentic.py` — example)

Runs in execution environment. `respond_node` calls `invoke_agent("conversationalist", ...)`. The conversationalist agent is a PydanticAI agent with tools, memory, and access to internal APIs. It returns the same `ChatResponse` structure so the graph handles routing identically.

```python
class ChatResponse(BaseModel):
    content: str
    route: Literal["chat", "planner", "specialist"] | None = None
    specialist: str | None = None
```

Ship this as an example with documentation noting it requires a `conversationalist` agent registered in the execution environment.

---

## Triage Graph Removal

Once triage logic is absorbed into the chat graph as `triage_node`, the standalone triage graph should be removed or deprecated. Confirm with the developer whether anything else depends on it before deleting.

---

## CLI: Textual REPL

Replace the current input mechanism with a Textual TUI. Click remains as the entry point and initialises the Textual app — no changes to CLI argument parsing or subcommand structure.

Textual is chosen over `prompt_toolkit` for two reasons: it can render dynamic interrupt payloads as interactive forms (radio, multi-select, text area) and return structured data to resume the graph, and it provides a richer chat output surface (markdown rendering, scrollback, streaming tokens). These are first-class requirements of the framework, not nice-to-haves.

### Slash Command Completions

Textual's `Input` widget accepts a `suggester` parameter for inline ghost-text completion. Wire a registry-driven suggester to the chat input:

```python
from textual.suggester import Suggester

class RegistrySuggester(Suggester):
    def __init__(self, registry):
        self.registry = registry
        super().__init__()

    async def get_suggestion(self, value: str) -> str | None:
        if not value.startswith("/"):
            return None
        return next(
            (cmd for cmd in self.registry.commands() if cmd.startswith(value)),
            None,
        )
```

In addition, expose slash commands through Textual's built-in `CommandPalette` (default `ctrl+p`). This gives two discovery surfaces: ghost-text for users who know the command prefix, and the palette for browsing the full registry. The palette is particularly useful since commands are registered dynamically from worker processes and users may not know what is available.

### Interrupt Form Rendering

When the graph returns a LangGraph interrupt payload, the Textual app pushes a modal `Screen` containing a dynamically constructed form. Field types map to Textual widgets:

- `radio` → `RadioSet`
- `multi_select` → `SelectionList`
- `text_area` → `TextArea`
- `text` → `Input`

On submission the screen is popped, the structured response dict is returned to the graph to resume the thread, and the chat view regains focus.

The interrupt payload schema the framework expects:

```python
class InterruptField(BaseModel):
    key: str
    label: str
    type: Literal["radio", "multi_select", "text_area", "text"]
    options: list[str] | None = None  # for radio and multi_select
    default: Any | None = None

class InterruptPayload(BaseModel):
    title: str
    description: str | None = None
    fields: list[InterruptField]
```

The form renderer reads this schema and constructs the screen dynamically — no hardcoded form layouts.

### Click Integration

Click initialises the REPL by calling `app.run()` on the Textual app. The REPL command stays minimal:

```python
@cli.command()
def chat():
    """Start the interactive chat session."""
    ChatApp(registry=registry).run()
```

No other changes to Click command structure.

### Registry Interface

Both the suggester and the command palette expect `registry.commands()` to return an iterable of strings in the format `/command` or `/specialist:mode`. The registry is populated from agents registered at runtime.

Confirm the existing registry interface before implementing. If `commands()` does not exist, add it to the registry class rather than working around it in the UI layer.

### Dependency

Add `textual` to project dependencies if not already present. Remove `prompt_toolkit` if it was added solely for this purpose.

---

## Functional Requirements

1. `parse_command_node` performs no LLM calls under any circumstance
2. `triage_node` uses a small/fast model — not the same model as `respond_node`
3. `respond_node` in the default implementation has zero dependency on `invoke_agent` or any registered agent
4. Both chat graph implementations satisfy `StateGraph[ChatState]` and are returned uncompiled
5. The config key resolves the graph factory via dotted import path at startup, not at request time
6. Slash command completions reflect the live registry state — not a hardcoded list
7. `_build_context` does not truncate message content — that responsibility belongs to the receiving agent
8. Unknown slash commands produce an inline error response from `respond_node`, not a graph error
9. The standalone triage graph is not removed until confirmed that nothing else depends on it
10. The agentic chat graph is shipped as a documented example, not wired in as a default
11. Interrupt payloads are rendered as dynamic Textual forms — field types map to widgets per the `InterruptPayload` schema
12. Form submission returns a structured dict to resume the LangGraph thread — the form renderer does not know about graph internals
13. Click is not replaced — it initialises the Textual app and nothing more

---

## Non-Functional Requirements

- `parse_command_node` must complete in under 5ms
- Triage model choice should be configurable — do not hardcode a model string in `triage_node`
- No new dependencies introduced beyond `textual` and `pydantic-ai` (confirm if already present)
- `prompt_toolkit` should not be added as a dependency — Textual supersedes it for this use case

---

## Three-Tier Boundary System

Always:
- Read existing files before modifying them
- Return uncompiled `StateGraph` from both `build_chat_graph` implementations
- Pass full message history to `invoke_agent` calls without truncation
- Use `_build_context` consistently across planner and specialist nodes

Ask first:
- Removing the standalone triage graph
- Changing the `ChatState` schema in ways that affect Aegra checkpointing
- Adding any node not described in this spec

Never:
- Call `invoke_agent` from `respond_node` in the default chat graph implementation
- Hardcode slash commands in the completer
- Compile the graph inside `build_chat_graph` — Aegra owns compilation
- Truncate message content in `_build_context`

---

## Open Questions

1. What is the current location and interface of the standalone triage graph?
2. What is the current location of the CLI REPL input loop and how is it currently structured?
3. Is `pydantic-ai` already a dependency?
4. Is `textual` already a dependency?
5. What does `registry.commands()` currently return, or does this method need to be added?
6. Is there a `get_llm()` factory or equivalent already in the codebase?
7. What model configuration mechanism exists — is model selection already config-driven?
8. What is the current interrupt payload structure returned by LangGraph — does it already conform to the `InterruptPayload` schema or does it need mapping?