"""One Bailian host, one key, two roles, one client.

Ranking posts to `/systemone` with raw HTTP because the payload is not a chat
completion. Descriptions use the OpenAI SDK against the same `base_url`. `Bailian`
owns both transports for the whole run, so base URL, model ids, auth, retries and
lifetimes stay out of `rank.py` and `describe.py`.
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


class Bailian:
    """The decision and chat transports, opened once and closed together."""

    def __init__(
        self,
        settings: BailianSettings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if settings.api_key is None:
            raise BailianError("PAPERFEED_BAILIAN__API_KEY is not set")
        key = settings.api_key.get_secret_value()
        self._settings = settings
        self._http = httpx.Client(
            headers={"Authorization": f"Bearer {key}", "User-Agent": USER_AGENT},
            timeout=DECISION_TIMEOUT,
            transport=transport,
        )
        self._chat = OpenAI(
            base_url=settings.base_url,
            api_key=key,
            timeout=CHAT_TIMEOUT,
            max_retries=2,
        )

    def __enter__(self) -> Bailian:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()
        self._chat.close()

    def decide(self, payload: dict[str, Any]) -> dict[str, Any]:
        """One System One decision call under this client's decision model."""
        url = f"{self._settings.base_url.rstrip('/')}/systemone"
        body = {**payload, "model": self._settings.decision_model}
        try:
            response = net.post(self._http, url, body)
        except net.Transient as exc:
            raise BailianError(str(exc)) from exc

        if response.status_code >= 400:
            raise BailianError(f"HTTP {response.status_code}: {response.text[:500]}")
        return _json_object(response)

    def chat(self, *, system: str, user: str, max_tokens: int) -> str:
        """One chat completion. Returns stripped text; raises if the model returns nothing."""
        response = self._chat.chat.completions.create(
            model=self._settings.chat_model,
            temperature=0.3,
            max_tokens=max_tokens,
            extra_body=self._settings.chat_extra_body or None,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        content = (response.choices[0].message.content or "").strip()
        if not content:
            raise BailianError("empty completion")
        return content


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise BailianError(f"response is not JSON: {response.text[:200]}") from exc
    if not isinstance(body, dict):
        raise BailianError("response is not a JSON object")
    return body
