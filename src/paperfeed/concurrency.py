"""Bounded thread-pool map for the per-item network calls."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


def map_bounded(fn: Callable[[T], R], items: Sequence[T], *, limit: int) -> list[R]:
    """Map `fn` over `items` with between 1 and `limit` workers."""
    if not items:
        return []
    workers = max(1, min(limit, len(items)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, items))
