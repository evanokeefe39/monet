# Chat TUI Requirements

## Layout constraints

The chat TUI uses a fixed vertical layout. These rules are non-negotiable.

### Welcome screen
- Must be visually centered both horizontally and vertically using `align: center middle` on the overlay container.
- Do NOT use Textual `Center`/`Middle` container widgets — use CSS `align` on the `WelcomeOverlay` itself.
- The `#welcome-card` uses `text-align: center`.
- Rich Text objects cannot use Textual CSS variables (`$primary`, `$accent`). Use hex color literals from the active palette.

### Prompt area (bottom stack)
From bottom of screen upward:
1. `StatusBar` — `dock: bottom`, height 1
2. `AutoGrowTextArea` (prompt) — `dock: bottom`, min-height 3, max-height 8
3. `#slash-suggest` (OptionList) — `layer: overlay`, `dock: bottom`, only visible when typing a `/` prefix. Must float ABOVE the prompt without displacing other widgets. Uses `margin-bottom` to clear the prompt+status area.

The slash-suggest dropdown must NEVER appear below the prompt or push the prompt downward. It is an overlay that grows upward from just above the prompt.

### Transcript
- `Transcript` fills remaining vertical space (`height: 1fr`).
- `WelcomeOverlay` lives inside Transcript on the `overlay` layer.

## Color palettes

All colors derive from `color-palettes.txt` at project root. Six themes are defined in `_themes.py`:
- `monet-dark` (default) — palette 1: teal/blue/coral
- `monet-retro` — palette 2: crimson/teal
- `monet-vivid` — palette 3: bright cyan/scarlet
- `monet-forest` — palette 8: deep forest/navy
- `monet-ember` — palette 9: terracotta/ocean
- `monet-light` — fallback light mode

Tag styles in `_view.py` use hex literals, not Textual CSS variables. When changing colors, pick from `color-palettes.txt` palettes.
