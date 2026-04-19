"""Tests for the random chat-thread name generator."""

from __future__ import annotations

import random

from monet.cli._namegen import random_chat_name


def test_random_chat_name_has_expected_shape() -> None:
    """Hyphen-separated ``<adjective>-<noun>-<8 hex chars>``."""
    name = random_chat_name()
    parts = name.split("-")
    assert len(parts) == 3
    adjective, noun, suffix = parts
    for word in (adjective, noun):
        assert word
        assert word == word.lower()
        assert word.isalpha()
    assert len(suffix) == 8
    assert all(c in "0123456789abcdef" for c in suffix)


def test_random_chat_name_word_pair_is_deterministic_with_seeded_rng() -> None:
    """Seeded rng pins the word pair; the hex suffix still varies."""
    rng = random.Random(42)
    first = random_chat_name(rng=rng)
    rng2 = random.Random(42)
    second = random_chat_name(rng=rng2)
    first_pair = "-".join(first.split("-")[:2])
    second_pair = "-".join(second.split("-")[:2])
    assert first_pair == second_pair
    # Suffixes are from secrets, not seedable — should differ.
    first_suffix = first.split("-")[2]
    second_suffix = second.split("-")[2]
    assert first_suffix != second_suffix


def test_random_chat_name_suffix_makes_identical_pairs_unique() -> None:
    """Even drawing the same word pair twice gives distinct full names."""
    rng_a = random.Random(1)
    rng_b = random.Random(1)
    assert random_chat_name(rng=rng_a) != random_chat_name(rng=rng_b)


def test_random_chat_name_produces_variety() -> None:
    names = {random_chat_name() for _ in range(50)}
    # Every name should be unique thanks to the hex suffix.
    assert len(names) == 50
