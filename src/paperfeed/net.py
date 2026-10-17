"""Shared HTTP mechanics for the Bailian and Feishu clients.

Both clients POST JSON to one URL. Only Bailian retries transient failures: a
retried webhook POST can deliver the same card twice, so Feishu sends once.
Non-retryable HTTP responses are returned unchanged; each caller decides what a
4xx means for its own API.
"""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

RETRY_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3


class Transient(Exception):
    """A failure that retrying may fix: network error or a retryable status."""


def post_once(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    timeout: httpx.Timeout,
    client: httpx.Client | None = None,
) -> httpx.Response:
    """POST JSON once. Raises `Transient` on a network error, never on a status."""
    http = client or httpx
    try:
        return http.post(url, json=payload, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        raise Transient(f"{type(exc).__name__}: {exc}") from exc


def post(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    timeout: httpx.Timeout,
    client: httpx.Client | None = None,
) -> httpx.Response:
    """POST JSON, retrying transient failures. Returns any non-retryable response."""

    def once() -> httpx.Response:
        response = post_once(url, payload, headers=headers, timeout=timeout, client=client)
        if response.status_code in RETRY_STATUS:
            raise Transient(f"HTTP {response.status_code}: {response.text[:200]}")
        return response

    retryer = Retrying(
        stop=stop_after_attempt(MAX_ATTEMPTS),
        wait=wait_exponential_jitter(initial=1, max=8),
        retry=retry_if_exception_type(Transient),
        reraise=True,
    )
    try:
        return retryer(once)
    except Transient as exc:
        raise Transient(f"gave up after {MAX_ATTEMPTS} attempts: {exc}") from exc
