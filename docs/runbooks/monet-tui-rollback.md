# Runbook: monet-tui rollback

Use this when `monet-tui` (the Go TUI) is broken and you need to fall back
to a known-good release while a fix is prepared.

## Symptoms

- `monet-tui` exits immediately with a non-zero code
- `monet-tui` fails to connect to the server
- The TUI renders incorrectly or input is unresponsive
- Wire compat CI fails after a server update

## Step 1 — confirm the binary is the problem

```bash
monet-tui --version          # check version string
monet status                 # confirm server is reachable
curl -s http://localhost:2026/api/v1/health | python3 -m json.tool
```

If `monet status` fails, the server is the problem, not the TUI.

## Step 2 — fall back to a known-good release

Check available releases:

```bash
gh release list --repo evanokeefe39/monet-tui
```

Download and install the previous release:

```bash
VERSION=vX.Y.Z   # replace with previous good version
OS=linux         # linux | darwin | windows
ARCH=amd64       # amd64 | arm64

gh release download $VERSION \
  --repo evanokeefe39/monet-tui \
  --pattern "monet-tui_${VERSION#v}_${OS}_${ARCH}.tar.gz" \
  --output /tmp/monet-tui.tar.gz

tar xf /tmp/monet-tui.tar.gz -C /usr/local/bin monet-tui
monet-tui --version   # confirm downgrade
```

## Step 3 — fall back to Python Textual TUI (emergency only)

If no good Go binary is available and you have a Python monet install:

```bash
uv run monet chat   # Python Textual TUI, still present until parity
```

This requires the `monet` Python package and all its dependencies installed.

## Step 4 — file an issue

Open a bug report with:
- `monet-tui --version` output
- `monet status` output
- The error or unexpected behaviour
- Server version (`GET /api/v1/health` → `version` field)

## Wire compatibility failures

If the failure is a wire compat CI failure after a server change:

1. Run `uv run pytest tests/compat/test_wire_compat.py -v` — identifies
   which Python type changed.
2. Run `go test ./go/tests/contract/... -v` — identifies which Go type
   diverged.
3. Determine which side changed (server PR or client PR).
4. Update `tests/compat/wire_schema.json` if the change is intentional,
   then update the diverging side to match.
5. Both compat tests must pass before the branch can merge.
