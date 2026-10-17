"""One Bailian host, one key, two roles.

Ranking posts to `/systemone` with raw HTTP because the payload is not a chat
completion. Descriptions use the OpenAI SDK against the same `base_url`. Keeping
base URL, auth, retries and client construction here means no host or model id
leaks into `rank.py` or `describe.py`.
"""

from __future__ import annotations

from typing import Any

import httpx
from openai import OpenAI

from . import USER_AGENT, net
from .config import BailianSettings
from .errors import PaperfeedError

DECISION_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
CHAT_TIMEOUT = 120.0  # seconds; the OpenAI SDK owns the connect/read split


class BailianError(PaperfeedError):
    """A call failed for a reason retrying will not fix."""


def _key(settings: BailianSettings) -> str:
    if settings.api_key is None:
        raise BailianError("PAPERFEED_BAILIAN__API_KEY is not set")
    return settings.api_key.get_secret_value()


def headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    api_key: str,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """POST JSON, retrying transient failures three times in total."""
    try:
        response = net.post(
            url,
            payload,
            headers=headers(api_key),
            timeout=DECISION_TIMEOUT,
            client=client,
        )
    except net.Transient as exc:
        raise BailianError(str(exc)) from exc

    if response.status_code >= 400:
        raise BailianError(f"HTTP {response.status_code}: {response.text[:500]}")

    try:
        body = response.json()
    except ValueError as exc:
        raise BailianError(f"response is not JSON: {response.text[:200]}") from exc
    if not isinstance(body, dict):
        raise BailianError("response is not a JSON object")
    return body


def decide(
    settings: BailianSettings,
    payload: dict[str, Any],
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """One System One decision call, optionally over a shared HTTP client."""
    url = f"{settings.base_url.rstrip('/')}/systemone"
    return post_json(url, payload, api_key=_key(settings), client=client)


def openai_client(settings: BailianSettings) -> OpenAI:
    return OpenAI(
        base_url=settings.base_url,
        api_key=_key(settings),
        timeout=CHAT_TIMEOUT,
        max_retries=2,
    )


def chat(
    settings: BailianSettings,
    *,
    system: str,
    user: str,
    max_tokens: int,
    client: OpenAI | None = None,
) -> str:
    """One description. Returns stripped text; raises if the model returns nothing."""
    owned = client is None
    if client is None:
        client = openai_client(settings)
    try:
        response = client.chat.completions.create(
            model=settings.chat_model,
            temperature=0.3,
            max_tokens=max_tokens,
            extra_body=settings.chat_extra_body or None,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        content = (response.choices[0].message.content or "").strip()
        if not content:
            raise BailianError("empty completion")
        return content
    finally:
        if owned:
            client.close()
