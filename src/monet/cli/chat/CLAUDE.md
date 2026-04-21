# monet.cli.chat — Textual TUI

## Layout (non-negotiable)

Fixed vertical stack, bottom → top:
1. `StatusBar` — `dock: bottom`, height 1
2. `AutoGrowTextArea` — `dock: bottom`, min-height 3, max-height 8
3. `#slash-suggest` (OptionList) — `layer: overlay`, `dock: bottom`. Floats ABOVE prompt via `margin-bottom`. Never pushes prompt downward.
4. `Transcript` — `height: 1fr`, fills remaining space. `WelcomeOverlay` inside on `overlay` layer.

Welcome screen: `align: center middle` on `WelcomeOverlay` container. Do NOT use Textual `Center`/`Middle` widgets. `#welcome-card` uses `text-align: center`.

Rich Text objects cannot use Textual CSS variables (`$primary`, `$accent`). Use hex literals from active palette.

## Color palettes

All colors from `color-palettes.txt` at project root. Themes in `_themes.py`:

| Theme | Palette | Colors |
|-------|---------|--------|
| `monet-dark` (default) | 1 | teal/blue/coral |
| `monet-retro` | 2 | crimson/teal |
| `monet-vivid` | 3 | bright cyan/scarlet |
| `monet-forest` | 8 | deep forest/navy |
| `monet-ember` | 9 | terracotta/ocean |
| `monet-light` | — | light mode fallback |

Tag styles in `_view.py` use hex literals. When changing colors, pick from `color-palettes.txt`.

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
