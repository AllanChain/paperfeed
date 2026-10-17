"""Shared text normalization for titles, abstracts, prompts and card text."""

from __future__ import annotations

import re

_WHITESPACE = re.compile(r"\s+")


def collapse_whitespace(text: str) -> str:
    """Collapse every whitespace run to one space and trim the ends."""
    return _WHITESPACE.sub(" ", text).strip()
