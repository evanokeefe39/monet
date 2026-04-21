# agent-recruitment

Reference pipelines for **agent discovery**, **trial**, and **performance
management** — all three driven by monet's default planner composing
capability agents, with no custom graphs.

The example ships two new capability agents (`code_executor`,
`data_analyst`), reuses the core `evaluator(compare)` comparator command, and
registers one `after_agent` hook (`record_run_summary`) that makes
invocation telemetry queryable through the artifact store.

## The three use cases, one topic

Discovery, trial, and performance management are not three pipelines.
They are one topic that the planner composes into a five-node DAG:

```
researcher(deep)               writes candidate_brief
        │
code_executor(eval_all)        runs each candidate in a subprocess sandbox
        │                       writes trial_scorecard
evaluator(compare)                       baseline + comparative ranking
        │                       writes comparative_review
data_analyst(score_agents)     scores the roster over the window
        │                       writes roster_scorecard
writer(deep)                   rolls up a weekly report
```

Reference topic (paste into `/plan` or `--topic`):

> Each week: (1) scan public sources for recently-released agent
> patterns and write a candidate brief; (2) evaluate every candidate in
> that brief against `fixtures/harness.json` with
> `code_executor(eval_all)` and compare the results against
> `fixtures/baseline.json` via `evaluator(compare)` to pick a recommended
> candidate; (3) in parallel, score every agent currently in the roster
> over the last 7 days using `data_analyst(score_agents)` and flag
> underperformers with replacement recommendations drawn from the
> recommended candidate.

## Layout

```
examples/agent-recruitment/
├── README.md
├── pyproject.toml
├── monet.toml                   # inherits default entrypoints
├── aegra.json                   # registers default + execution graphs
├── server_graphs.py             # 0-arg Aegra factories + infra boot
├── .monet/docker-compose.yml    # canonical ports 5432 / 6379 / 2026
├── fixtures/
│   ├── harness.json             # shared fixture + assertions
│   └── baseline.json            # evaluator(compare) thresholds + criteria
└── src/recruitment/
    ├── agents/
    │   ├── code_executor.py     # run / eval_all
    │   └── data_analyst.py      # query / score_agents
    ├── hooks.py                 # record_run_summary (after_agent)
    ├── sandbox.py               # subprocess + tempdir helper
    └── schemas.py               # pydantic models
```

## Running locally

```bash
cp .env.example .env            # set API keys for the reference planner/researcher
uv pip install -e .
monet dev                       # provisions Postgres + starts Aegra
monet chat
# > /plan <paste the reference topic above>
# > approve
```

On approve the chat run writes the `work_brief` artifact and runs
execution end-to-end. Open any artifact URL emitted in the transcript
(`/api/v1/artifacts/<id>/view`) to see the candidate brief, the trial
scorecard, the comparative review, and the roster scorecard.

## Plan-freeze workflow (recurring fires)

The intended recurring-work pattern separates **plan iteration** from
**plan execution**:

1. Iterate the plan in `monet chat` until the planner reliably produces
   the right DAG. Approve once.
2. Capture the `work_brief_pointer` the planning subgraph printed.
3. Fire the frozen DAG directly via the invocable `execution` graph:

```bash
monet run --graph execution --input '{
  "work_brief_pointer": {"artifact_id": "…", "url": "…", "key": "work_brief"},
  "routing_skeleton": { ... the skeleton from the approved run ... }
}'
```

Stage 2 has no planning step, no HITL approval gate, and no
`--auto-approve` flag. Each run re-queries the world inside every
agent invocation (researcher re-scans sources, data_analyst re-queries
the artifact index); the DAG shape is frozen, the agent behaviour is
not.

When the Priority 3 scheduler ships, the same payload drops into:

```bash
monet schedule add recruitment-weekly \
  --graph execution \
  --input '<same JSON as above>' \
  --cron "0 9 * * 1"
```

## Sandbox disclaimer + production path

`recruitment.sandbox.run_candidate_in_subprocess` is a developer-
ergonomic helper. It launches the candidate in the worker's own Python
interpreter inside a `TemporaryDirectory` and enforces a wall-clock
timeout. **It is not a security boundary.** Candidates can read env
vars, write outside the tmp dir, call the network, and exhaust host
resources.

Replace this helper before running untrusted candidates. Shape the
replacement to match the module's public function — accept a candidate +
fixture, return an `ExecutionReport` — and `code_executor` keeps working
unchanged. Proven production targets:

| Service | Fit |
|---|---|
| Modal (`modal.Sandbox`) | Ephemeral containers, Python SDK, clone-and-run from a Git URL. Typically the shortest Python-only path. |
| E2B Sandboxes | Firecracker microVMs via HTTP. |
| Cloud Run Jobs / AWS Fargate Task / Azure Container Apps Jobs | Cloud-native, reuse existing VPC + secrets manager. |
| Kubernetes Job + strict PodSecurityStandard | In-house fleets. |

The roadmap has a standalone item to ship a `modal_sandbox.py`
reference implementation once a concrete user materialises.

## Performance-mgmt note

`data_analyst(score_agents)` reads `run_summary`-tagged artifacts
written by the `record_run_summary` hook. Without that hook there is
nothing to score. In distributed deployments, run the score pass
against the `local` pool so the agent sees the full manifest; workers
only know about their own local capabilities.
