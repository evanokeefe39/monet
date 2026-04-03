# Agent Onboarding

Onboarding is the process of generating a monet client for an existing agent — a Python file containing `@agent` decorated functions that wrap the agent's existing entry points and translate between the agent's native invocation pattern and the monet input/output envelope. The result is a registered, documented, instrumented agent that the orchestrator can invoke without knowing anything about how the agent was built.

The onboarding mechanism works by reading the agent's existing documentation — wherever that lives — and generating the translation layer. The developer reviews the generated file, fills in any gaps the template could not infer, and the agent is integrated.

This document is for developers who have an existing agent and want to bring it into the monet orchestration system. It is not required reading for developers building new agents from scratch using the SDK directly.

---

## What onboarding produces

A single Python file per agent containing:

- One `@agent` decorated async function per discovered entry point
- Docstrings populated from the agent's existing documentation
- Correct calling conventions — synchronous for bounded commands, async subprocess or HTTP pattern for long-running commands
- `emit_progress()` calls in the right places — stdout loop for CLIs, polling loop for HTTP agents
- `run_id` and `trace_id` correlation passed to the agent where it supports it
- Typed exception raises for documented error conditions mapped to `NeedsHumanReview`, `EscalationRequired`, or `SemanticError`

The generated file uses the monet SDK throughout. The developer never interacts with the orchestration layer directly — the SDK handles `AgentResult` assembly, OTel instrumentation, content offloading, and signal propagation.

---

## What the developer does after generation

The generated file is scaffolding, not finished code. After generation the developer:

1. Reviews each stub — does the command name match the intent? Is the calling convention right?
2. Fills in the stub body — the translation from `task` and `context` to the agent's native invocation
3. Verifies typed exception mapping — do the agent's error conditions map correctly to monet signals?
4. Checks the summary output from the template — any inferred names or descriptions should be reviewed and corrected
5. Runs the tests — the SDK's mock agent fixtures cover the common paths

The stubs that are identical — all CLIs that simply forward the task and read stdout — can be filled in by applying a consistent pattern across all of them. Stubs that require custom translation logic will be obvious from the stub body.

---

## Onboarding templates

Templates live in `docs/prompts/onboard/`. Each targets a different agent documentation format. Use whichever matches the agent being onboarded. If the agent has multiple documentation sources (a README plus an OpenAPI spec, for example), use both templates and merge the outputs.

---

### `from-skill-files.md` — Claude Code, opencode, pi, and compatible tools

Use when the agent defines its entry points as markdown files with YAML frontmatter — Claude Code skills, opencode commands, pi skills, or any tool following the same convention.

```markdown
# Onboard a skill-based agent into monet

You are given a directory containing slash command or skill definitions
from an AI coding agent (Claude Code, opencode, pi, or similar tool).

## Step 1 — Discover entry points

For each file in the directory tree:

1. If the file is named SKILL.md or ends in .md with YAML frontmatter:
   - Extract `name` from frontmatter if present, otherwise use the
     filename stem (strip .md, convert hyphens to underscores)
   - Extract `description` from frontmatter if present, otherwise use
     the first non-empty line of the file body
   - Skip files with no identifiable name or description

2. Ignore files that are clearly not command definitions:
   README.md, AGENTS.md, CLAUDE.md, and similar project context files

3. Ignore supporting resource files — files without a `name` frontmatter
   field whose filename is not a plausible command name

## Step 2 — Generate stubs

For each discovered command generate a decorated async function:
- `@agent(agent_id="{AGENT_ID}")` for the fast command
- `@agent(agent_id="{AGENT_ID}", command="{name}")` for all others
- Function name: `{agent_id}_{name}` with hyphens replaced by underscores
- Parameters: `task: str, context: list`
- Docstring: the extracted description
- Body: `...` placeholder

Imports at the top of the file:
```python
from monet import agent, get_run_context, emit_progress, write_artifact
from monet import NeedsHumanReview, EscalationRequired, SemanticError
```

## Step 3 — Report

Print a summary:
- How many commands were found
- Which files were skipped and why
- Any names or descriptions that were inferred rather than explicit

## Inputs

Skills directory: $ARGUMENTS
Agent ID: $ARGUMENTS
Output file: $ARGUMENTS
```

---

### `from-cli-help.md` — Compiled CLIs and command-line tools

Use when the agent is a compiled binary or script with subcommands. Provide the output of running the CLI with `--help`, `help`, or equivalent. For CLIs with subcommands, include the top-level help and the help for each subcommand.

```markdown
# Onboard a CLI agent into monet

You are given the help output from a command-line agent — either the
output of running the CLI with --help, a man page excerpt, or a README
section describing its commands and flags.

## Step 1 — Discover entry points

Identify all commands or subcommands the CLI exposes. For each:
- Extract the command name
- Extract the description
- Extract the arguments and flags it accepts
- Determine whether it is synchronous (produces output immediately and
  exits) or long-running (runs for an extended period, may emit progress)

## Step 2 — Map to monet calling conventions

Synchronous commands → standard stub that awaits process completion and
returns stdout as the result.

Long-running commands → stub that reads stdout line by line in an async
loop, forwarding lines that look like progress indicators via
emit_progress(), and treating the final line as the result.

For both: use asyncio.create_subprocess_exec. Pass run_id from
get_run_context() as a CLI argument if the CLI accepts a correlation ID.

Map documented exit codes and error messages to typed exceptions:
- Permission or authorisation errors → EscalationRequired
- Quality or validation failures → SemanticError
- Requests for human input → NeedsHumanReview
- Unexpected errors → let the decorator catch and wrap as SemanticError

## Step 3 — Generate stubs

For each command generate a decorated async function with the pattern:

```python
@agent(agent_id="{AGENT_ID}", command="{name}")
async def {agent_id}_{name}(task: str, context: list):
    """
    {description}
    """
    ctx = get_run_context()
    proc = await asyncio.create_subprocess_exec(
        "./agent", "{name}", "--task", task,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    result_lines = []
    async for line in proc.stdout:
        decoded = line.decode().strip()
        emit_progress({"status": decoded})
        result_lines.append(decoded)
    await proc.wait()
    if proc.returncode != 0:
        raise SemanticError(type="cli_error", message=result_lines[-1])
    return result_lines[-1] if result_lines else ""
```

Adapt the subprocess arguments to match the CLI's actual invocation pattern.

## Step 4 — Report

Print a summary of commands found, calling convention assigned to each,
and any exit codes or errors that could not be mapped to a typed exception.

## Inputs

CLI help output (paste here): $ARGUMENTS
Agent ID: $ARGUMENTS
Output file: $ARGUMENTS
```

---

### `from-openapi.md` — HTTP services with an OpenAPI specification

Use when the agent is an HTTP service with an OpenAPI 3.x spec. This template generates the richest output because OpenAPI specs contain parameter schemas, response schemas, and error codes.

```markdown
# Onboard an HTTP agent with an OpenAPI spec into monet

You are given an OpenAPI 3.x specification for an HTTP agent service.

## Step 1 — Discover entry points

For each path and operation in the spec:
- Extract the operation ID or construct a name from the HTTP method
  and path (e.g., POST /analyse → analyse)
- Extract the summary or description
- Determine the calling convention:
  - Returns response body directly → synchronous command
  - Returns 202 Accepted with a task ID → async command
  - Long response time indicated in description → async command

## Step 2 — Map request schema to monet input

The monet input envelope provides `task` (string) and `context` (list
of typed entries). Map these to the operation's request schema:
- If the operation takes a single string prompt → map task directly
- If the operation takes structured input → construct the request body
  from task and relevant context entries
- Note in a comment any request fields that cannot be inferred from
  task and context — the developer must fill these in

## Step 3 — Map response schema to AgentResult

- Inline response body → return as string (decorator handles content offload)
- Response with artifact URLs → call write_artifact() for each, return pointers
- Error responses → map to typed exceptions by status code:
  - 403/401 → EscalationRequired
  - 422/400 with validation detail → SemanticError
  - 202 polling pattern → async stub with polling loop

## Step 4 — Generate stubs

For synchronous operations:

```python
@agent(agent_id="{AGENT_ID}", command="{name}")
async def {agent_id}_{name}(task: str, context: list):
    """
    {description}
    """
    ctx = get_run_context()
    response = await http_client.post(
        "{base_url}/{path}",
        headers={"traceparent": ctx.trace_id},
        json={"task": task}  # TODO: map context entries to request fields
    )
    if response.status_code == 403:
        raise EscalationRequired(reason="insufficient permissions")
    response.raise_for_status()
    return response.json().get("result", "")
```

For async 202 operations include the polling loop pattern.

## Step 5 — Report

Print a summary of operations found, calling convention assigned to each,
request fields that could not be mapped automatically, and error codes
that could not be mapped to typed exceptions.

## Inputs

OpenAPI spec (paste JSON or YAML): $ARGUMENTS
Agent ID: $ARGUMENTS
Base URL: $ARGUMENTS
Output file: $ARGUMENTS
```

---

### `from-readme.md` — Agents documented only in a README or markdown docs

Use when the agent has no structured command definitions but is documented in a README, wiki page, or other unstructured markdown. This template uses looser heuristics and produces output that requires more developer review.

```markdown
# Onboard an agent documented in a README into monet

You are given documentation for an agent in the form of a README,
wiki page, or similar unstructured markdown.

## Step 1 — Identify entry points

Look for:
- Section headings that describe commands, operations, or features
- Code blocks showing example invocations
- Bullet lists of capabilities or commands
- Any table listing commands with descriptions

For each identified entry point extract:
- A name (normalise to lowercase with hyphens)
- A description (one sentence)
- Whether it takes arguments and what kind
- Whether it is synchronous or long-running

Mark any entry points where you had to infer the name or description
as low confidence — flag these in the report for developer review.

## Step 2 — Generate stubs

Generate the same stub structure as the other templates. For entry
points where the invocation pattern is unclear, use a TODO comment:

```python
@agent(agent_id="{AGENT_ID}", command="{name}")
async def {agent_id}_{name}(task: str, context: list):
    """
    {description}
    """
    # TODO: implement — invocation pattern unclear from documentation
    # Review the README section: {section_reference}
    ...
```

## Step 3 — Report

This template produces the least reliable output. The report must include:
- Every entry point found with confidence level (high/medium/low)
- Specific README sections that should be reviewed before implementing
  each low-confidence stub
- Any capabilities mentioned in the README that could not be mapped
  to a clear entry point

## Inputs

README or documentation (paste here): $ARGUMENTS
Agent ID: $ARGUMENTS
Output file: $ARGUMENTS
```

---

## Using multiple templates together

An agent may have documentation in multiple formats. A common case is a CLI tool that also has an OpenAPI spec for its HTTP mode, or a pi agent that has both skill files and a README explaining advanced usage.

Run each applicable template and merge the output files. Remove duplicate stubs — keep the one from the richer source (OpenAPI over README, skill files over CLI help). The combined file gives the most complete coverage.

---

## What the onboarding mechanism is not

Onboarding generates a client, not the agent itself. The generated file is the translation layer between the monet orchestration system and the agent's existing interface. The agent continues to run exactly as it did before. Nothing inside the agent changes. The agent does not need to know it is being orchestrated by monet.

This is the blackbox principle applied to onboarding. The orchestrator is agnostic to how the agent was built. The onboarding mechanism is agnostic to which tool the agent was built with. The only thing that matters is that the agent has documented entry points and the developer can describe them to the template.

---

## Relationship to the SDK

The generated file uses the full SDK. Every generated stub has access to:

- `get_run_context()` — for `run_id`, `trace_id`, `agent_id`, `command`
- `emit_progress()` — for forwarding progress events into the LangGraph stream
- `write_artifact()` — for writing large outputs to the catalogue
- `NeedsHumanReview`, `EscalationRequired`, `SemanticError` — for signal emission
- Automatic content offloading — large return values are offloaded transparently

The developer filling in stub bodies has the full SDK available. They are not writing glue code — they are writing agent integration code with a complete set of primitives.

---

## Adding the onboarding output to the capability descriptor

After the generated file is reviewed and complete, the agent's commands are registered in the SDK registry via the `@agent` decorator. The capability descriptor for the agent should be created alongside the generated file, specifying the calling convention, SLA characteristics, and retry policy for each command.

The docstrings generated from the agent's documentation become the command descriptions in the capability descriptor and are available to the planner when reasoning about which agent and command to invoke.
