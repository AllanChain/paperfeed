"""Errors the CLI can report without a traceback."""

from __future__ import annotations


class PaperfeedError(RuntimeError):
    """A failure caused by configuration or an unavailable external service."""
