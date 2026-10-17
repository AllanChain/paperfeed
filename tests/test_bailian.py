from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import tenacity.nap

from conftest import make_settings
from paperfeed import bailian
from paperfeed.bailian import BailianError, chat, decide, headers, post_json

URL = "https://example.invalid/v1/systemone"


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tenacity.nap.time, "sleep", lambda _seconds: None)


def _client(responses: list[httpx.Response]) -> tuple[httpx.Client, list[int]]:
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return responses[min(len(calls) - 1, len(responses) - 1)]

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_headers_use_bearer() -> None:
    assert headers("sk-123")["Authorization"] == "Bearer sk-123"


def test_post_json_retries_a_429() -> None:
    client, calls = _client(
        [httpx.Response(429, text="slow down"), httpx.Response(200, json={"ok": True})]
    )
    assert post_json(URL, {"a": 1}, api_key="k", client=client) == {"ok": True}
    assert len(calls) == 2


def test_post_json_does_not_retry_a_401() -> None:
    client, calls = _client([httpx.Response(401, text="bad key")])
    with pytest.raises(BailianError, match="401"):
        post_json(URL, {"a": 1}, api_key="k", client=client)
    assert len(calls) == 1


def test_post_json_gives_up_after_three_attempts() -> None:
    client, calls = _client([httpx.Response(503, text="unavailable")])
    with pytest.raises(BailianError, match="gave up after 3 attempts.*503"):
        post_json(URL, {"a": 1}, api_key="k", client=client)
    assert len(calls) == 3


def test_post_json_rejects_a_non_object_body() -> None:
    client, _ = _client([httpx.Response(200, json=[1, 2, 3])])
    with pytest.raises(BailianError, match="not a JSON object"):
        post_json(URL, {"a": 1}, api_key="k", client=client)


def test_decide_posts_to_systemone(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake_post(url, payload, *, api_key, client=None):
        seen.update(url=url, api_key=api_key, payload=payload)
        return {"answers": {}}

    monkeypatch.setattr(bailian, "post_json", fake_post)
    settings = make_settings().bailian

    assert decide(settings, {"model": "m"}) == {"answers": {}}
    assert seen["url"] == f"{settings.base_url.rstrip('/')}/systemone"
    assert seen["api_key"] == "bailian-key"


def test_missing_key_is_reported() -> None:
    settings = make_settings(bailian={"api_key": None}).bailian
    with pytest.raises(BailianError, match="API_KEY"):
        decide(settings, {"model": "m", "state": {}, "questions": {}})


class FakeCompletions:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class FakeClient:
    def __init__(self, content: str = " ok ") -> None:
        self.completions = FakeCompletions(content)
        self.chat = SimpleNamespace(completions=self.completions)


def test_chat_sends_model_and_thinking_flag() -> None:
    settings = make_settings().bailian
    client = FakeClient(" A description. ")
    assert chat(settings, system="s", user="u", max_tokens=8, client=client) == "A description."

    call = client.completions.calls[0]
    assert call["model"] == "qwen3.8-flash"
    assert call["extra_body"] == {"enable_thinking": False}
    assert call["messages"][0] == {"role": "system", "content": "s"}


def test_chat_rejects_an_empty_completion() -> None:
    settings = make_settings().bailian
    with pytest.raises(BailianError, match="empty"):
        chat(settings, system="s", user="u", max_tokens=8, client=FakeClient("   "))
