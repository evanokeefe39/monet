# monet.cli.chat — Textual TUI

## Layout (non-negotiable)

Fixed vertical stack, bottom → top:
1. `StatusBar` — `dock: bottom`, height 1
2. `AutoGrowTextArea` — `dock: bottom`, min-height 3, max-height 8
3. `#slash-suggest` (OptionList) — `layer: overlay`, `dock: bottom`. Floats ABOVE prompt via `margin-bottom`. Never pushes prompt downward.
4. `Transcript` — `height: 1fr`, fills remaining space. `WelcomeOverlay` inside on `overlay` layer.

Welcome screen: `align: center middle` on `WelcomeOverlay` container. Do NOT use Textual `Center`/`Middle` widgets. `#welcome-card` uses `text-align: center`.

Rich Text objects cannot use Textual CSS variables (`$primary`, `$accent`). Use hex from `MONET_EMBER` via module-level constants in `_view.py`.

## Color architecture

Active theme: `MONET_EMBER` (palette 9, terracotta/ocean). All themes defined in `_themes.py`, registered on mount.

Two color paths:
1. **Textual CSS** (`DEFAULT_CSS` blocks): use `$primary`, `$accent`, `$surface` etc. Theme-responsive.
2. **Rich Text** (`styled_line`, status bar, welcome, screens): use named module-level constants sourced from `MONET_EMBER` in `_view.py` (`_PRIMARY`, `_ACCENT`, `_ERROR`, `_SECONDARY`, `_MUTED`). Other consumers import from `MONET_EMBER.variables` directly.

When changing colors, pick from `color-palettes.txt`. All hex in Rich Text paths must trace back to `_themes.py`.

## Key files

| File | Owns |
|------|------|
| `_app.py` | Textual `App` subclass, mounts layout |
| `_view.py` | Transcript widget, tag rendering |
| `_prompt.py` | `AutoGrowTextArea`, slash-suggest logic |
| `_status_bar.py` | `StatusBar` widget |
| `_screens.py` | Welcome overlay |
| `_turn.py` | Turn model — user/assistant message pair |
| `_transcript.py` | Transcript state |
| `_commands.py` | Slash-command dispatch |
| `_cli.py` | Click entry point |
| `_constants.py` | Theme names, key bindings |

## Running tests

Never run the full project test suite when only chat files changed — run `tests/chat/` or individual test files. Run test files one at a time sequentially to isolate hangs. Every Bash call that runs tests MUST set an explicit `timeout` (30s for unit tests, 60s for Textual app tests). Never use `run_in_background` for tests — a backgrounded test with no timeout accumulates silently. If a test hasn't finished within its timeout, that's a signal to investigate, not retry.

## HITL

HITL interrupts render as transcript text. Next user submission resumes the run. No separate HITL widget — inline in transcript flow.
