"""Custom Textual themes for the monet chat TUI.

Palettes sourced from color-palettes.txt. Dark-first identity — light
variant is a fallback, not a second product.

Registered on app mount via ``self.register_theme(...)``; the active
theme is selected by ``self.theme = "monet-ember"``.
"""

from __future__ import annotations

from textual.theme import Theme

# Palette 1: teal/blue/coral
MONET_DARK = Theme(
    name="monet-dark",
    primary="#168b9f",
    secondary="#46b2e4",
    accent="#2d8db5",
    warning="#ae6002",  # "#c47445"
    error="#d04936",
    success="#ea584b",
    foreground="#e0e0e8",
    background="#000000",
    surface="#0a0a12",
    panel="#14141e",
    boost="#1e1e2a",
    dark=True,
    variables={
        "text-muted": "#7a7a85",
        "panel-lighten-1": "#1e1e2a",
        "panel-lighten-2": "#2a2a38",
        # transcript tag colors (sourced from palette 9)
        "tag-assistant": "#007065",
        "tag-info": "#004c79",
        "tag-error": "#ae463d",
        "tag-hint": "#00365f",
        "progress-rule": "#aaaaaa",
        # run-state / UI highlight colors
        "status-highlight": "#00c8da",
        "status-interrupted": "#e8a838",
        "status-error": "#e05050",
        "status-running": "#50b0e0",
    },
)

# Palette 2: deep crimson/teal
MONET_RETRO = Theme(
    name="monet-retro",
    primary="#008896",
    secondary="#00adbb",
    accent="#007977",
    warning="#d14e4c",
    error="#900000",
    success="#d03468",
    foreground="#e0e0e8",
    background="#000000",
    surface="#0a0a12",
    panel="#14141e",
    boost="#1e1e2a",
    dark=True,
    variables={
        "text-muted": "#7a7a85",
        "panel-lighten-1": "#1e1e2a",
        "panel-lighten-2": "#2a2a38",
    },
)

# Palette 3: bright cyan/scarlet
MONET_VIVID = Theme(
    name="monet-vivid",
    primary="#0095a1",
    secondary="#00c8da",
    accent="#00bfd5",
    warning="#d65f45",
    error="#d00722",
    success="#c4564a",
    foreground="#e0e0e8",
    background="#000000",
    surface="#0a0a12",
    panel="#14141e",
    boost="#1e1e2a",
    dark=True,
    variables={
        "text-muted": "#7a7a85",
        "panel-lighten-1": "#1e1e2a",
        "panel-lighten-2": "#2a2a38",
    },
)

# Palette 8: deep forest/navy
MONET_FOREST = Theme(
    name="monet-forest",
    primary="#587857",
    secondary="#006e30",
    accent="#00527b",
    warning="#523300",
    error="#004013",
    success="#225240",
    foreground="#c8d8c0",
    background="#000000",
    surface="#0a0f0a",
    panel="#101a10",
    boost="#182418",
    dark=True,
    variables={
        "text-muted": "#6a7a6a",
        "panel-lighten-1": "#182418",
        "panel-lighten-2": "#243024",
    },
)

# Palette 9: burnt terracotta/ocean
MONET_EMBER = Theme(
    name="monet-ember",
    primary="#007065",
    secondary="#004c79",
    accent="#ae6002",
    warning="#c6583c",
    error="#ae463d",
    success="#006246",
    foreground="#e0dcd8",
    background="#000000",
    surface="#120a08",
    panel="#1e1210",
    boost="#2a1a16",
    dark=True,
    variables={
        "text-muted": "#8a7a72",
        "panel-lighten-1": "#2a1a16",
        "panel-lighten-2": "#362420",
        "status-highlight": "#00c8da",
        # Primary shades (teal-green scale)
        "teal-50": "#dbfdfd",
        "teal-100": "#cef0ef",
        "teal-200": "#8ce7e5",
        "teal-300": "#6fc9c5",
        "teal-400": "#49a39c",
        "teal-500": "#228278",
        "teal-600": "#006a5f",
        "teal-700": "#00564a",
        "teal-800": "#004335",
        "teal-900": "#003025",
        "teal-950": "#001e14",
        # Secondary shades (ocean-blue scale)
        "blue-50": "#e5f7ff",
        "blue-100": "#d7e9ff",
        "blue-200": "#a8d8ff",
        "blue-300": "#89bbf5",
        "blue-400": "#6097cc",
        "blue-500": "#3a77a8",
        "blue-600": "#1c608e",
        "blue-700": "#004d77",
        "blue-800": "#003a62",
        "blue-900": "#002a44",
        "blue-950": "#001930",
        # Accent palette
        "accent-amber": "#e8a838",
        "accent-green": "#6dce82",
        "accent-cyan": "#00c9f5",
        "accent-purple": "#b9a3ff",
        "accent-rose": "#ff8ca9",
    },
)

MONET_LIGHT = Theme(
    name="monet-light",
    primary="#0f6d7d",
    secondary="#2178bd",
    accent="#1f7090",
    warning="#a35c36",
    error="#b33a2d",
    success="#c44840",
    foreground="#1a1a22",
    background="#f5f5f7",
    surface="#eaeaef",
    panel="#dedee5",
    boost="#d2d2da",
    dark=False,
    variables={
        "text-muted": "#6a6a78",
        "panel-lighten-1": "#d2d2da",
        "panel-lighten-2": "#c4c4cd",
    },
)

MONET_THEMES: tuple[Theme, ...] = (
    MONET_DARK,
    MONET_RETRO,
    MONET_VIVID,
    MONET_FOREST,
    MONET_EMBER,
    MONET_LIGHT,
)
