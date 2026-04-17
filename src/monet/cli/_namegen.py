"""Random chat-thread name generator.

Produces hyphen-separated names like ``dusty-fennel-a7f3a110``: an
adjective from a flavour/mood palette, a noun from a mixed
food / creature / object pool, and an 8-char hex suffix so even
identical word pairs stay unique across sessions. The word pair is the
memorable handle the user sees in the toolbar; the hex suffix prevents
collisions when two threads draw the same adjective+noun combination
(~900 word combos so collisions are rare but not impossible).

Used by :func:`monet.cli._chat._resolve_thread` to name new chats at
creation time so the server-side metadata carries a human-readable name
the TUI toolbar can display without falling back to ``untitled``.
"""

from __future__ import annotations

import random
import secrets

_ADJECTIVES: tuple[str, ...] = (
    "flaming",
    "spicy",
    "electric",
    "velvet",
    "cosmic",
    "quiet",
    "brave",
    "crimson",
    "golden",
    "frosty",
    "glowing",
    "dusty",
    "rumbling",
    "swift",
    "amber",
    "honeyed",
    "midnight",
    "silver",
    "clever",
    "whispering",
    "prowling",
    "wandering",
    "bold",
    "nimble",
    "gleaming",
    "fierce",
    "curious",
    "humble",
    "jaunty",
    "dappled",
)

_NOUNS: tuple[str, ...] = (
    "mojito",
    "baguette",
    "octopus",
    "tiger",
    "plum",
    "lantern",
    "falcon",
    "orchid",
    "comet",
    "harbor",
    "forge",
    "nebula",
    "otter",
    "compass",
    "mango",
    "dolphin",
    "ember",
    "willow",
    "stag",
    "canyon",
    "thistle",
    "anchor",
    "magnolia",
    "sparrow",
    "pebble",
    "quill",
    "fennel",
    "raven",
    "maple",
    "lotus",
)


def random_chat_name(*, rng: random.Random | None = None) -> str:
    """Return a name like ``dusty-fennel-a7f3a110``.

    Format: ``<adjective>-<noun>-<8 hex chars>``. The word pair is the
    memorable handle; the hex suffix guarantees uniqueness across
    sessions even when the word pair repeats.

    Args:
        rng: Optional :class:`random.Random` for reproducible word
            choice in tests. Omit in production so the default system
            RNG is used. The hex suffix is always drawn from
            :mod:`secrets` and is not affected by *rng*.
    """
    r = rng or random
    adjective = r.choice(_ADJECTIVES)
    noun = r.choice(_NOUNS)
    suffix = secrets.token_hex(4)
    return f"{adjective}-{noun}-{suffix}"


__all__ = ["random_chat_name"]
