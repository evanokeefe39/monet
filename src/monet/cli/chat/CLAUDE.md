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

## HITL

HITL interrupts render as transcript text. Next user submission resumes the run. No separate HITL widget — inline in transcript flow.
