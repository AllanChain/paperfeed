"""Shared HTTP mechanics for the Bailian, Feishu and arXiv clients.

Bailian and the arXiv listing fetches retry transient failures; both are
idempotent. Feishu sends once, because a retried webhook POST can deliver the
same card twice. Non-retryable HTTP responses are returned unchanged; each
caller decides what a 4xx means for its own API.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

RETRY_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3


class Transient(Exception):
    """A failure that retrying may fix: network error or a retryable status."""


@contextmanager
def open_client(
    *,
    headers: dict[str, str],
    timeout: httpx.Timeout | float,
    reuse: httpx.Client | None = None,
) -> Iterator[httpx.Client]:
    """Yield `reuse` when given, else a client with these defaults.

    A borrowed client is used as-is: its owner decides headers and timeout. Only
    the client this function creates is closed here.
    """
    if reuse is not None:
        yield reuse
        return
    with httpx.Client(headers=headers, timeout=timeout) as own:
        yield own


def post_once(client: httpx.Client, url: str, payload: dict[str, Any]) -> httpx.Response:
    """POST JSON once. Raises `Transient` on a network error, never on a status."""
    try:
        return client.post(url, json=payload)
    except httpx.HTTPError as exc:
        raise Transient(f"{type(exc).__name__}: {exc}") from exc


def _retry(
    once: Callable[[], httpx.Response],
    *,
    attempts: int,
    wait_seconds: float,
    wait_max: float,
) -> httpx.Response:
    """Call `once` until it stops raising `Transient` or the attempts run out."""
    retryer = Retrying(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential_jitter(multiplier=wait_seconds, max=wait_max),
        retry=retry_if_exception_type(Transient),
        reraise=True,
    )
    try:
        return retryer(once)
    except Transient as exc:
        raise Transient(f"gave up after {attempts} attempts: {exc}") from exc


def post(client: httpx.Client, url: str, payload: dict[str, Any]) -> httpx.Response:
    """POST JSON, retrying transient failures. Returns any non-retryable response."""

    def once() -> httpx.Response:
        response = post_once(client, url, payload)
        if response.status_code in RETRY_STATUS:
            raise Transient(f"HTTP {response.status_code}: {response.text[:200]}")
        return response

    return _retry(once, attempts=MAX_ATTEMPTS, wait_seconds=1, wait_max=8)


def get(
    client: httpx.Client,
    url: str,
    *,
    attempts: int = MAX_ATTEMPTS,
    wait_seconds: float = 1.0,
) -> httpx.Response:
    """GET, retrying transient failures. Returns any non-retryable response.

    `wait_seconds` is the first backoff; it doubles, with jitter, up to four
    times that. A rate-limited source needs a longer first wait than a hot API:
    arXiv's 429 means its per-IP limit is busy, not that the request is wrong.
    """

    def once() -> httpx.Response:
        try:
            response = client.get(url)
        except httpx.HTTPError as exc:
            raise Transient(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code in RETRY_STATUS:
            raise Transient(f"HTTP {response.status_code}: {response.text[:200]}")
        return response

    return _retry(
        once,
        attempts=attempts,
        wait_seconds=wait_seconds,
        wait_max=wait_seconds * 4,
    )
