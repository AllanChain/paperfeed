from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
import tenacity.nap

from conftest import make_settings
from paperfeed import bailian
from paperfeed.bailian import Bailian, BailianError


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tenacity.nap.time, "sleep", lambda _seconds: None)


def _bailian(responses: list[httpx.Response]) -> tuple[Bailian, list[httpx.Request]]:
    """A Bailian whose decision transport replays `responses`."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return responses[min(len(requests) - 1, len(responses) - 1)]

    transport = httpx.MockTransport(handler)
    return Bailian(make_settings().bailian, transport=transport), requests


def test_decide_injects_the_model_and_bearer_key() -> None:
    client, requests = _bailian([httpx.Response(200, json={"answers": {}})])

    assert client.decide({"state": {"title": "t"}, "questions": {}}) == {"answers": {}}

    request = requests[0]
    assert str(request.url) == "https://dashscope.aliyuncs.com/compatible-mode/v1/systemone"
    assert request.headers["authorization"] == "Bearer bailian-key"
    assert json.loads(request.content) == {
        "state": {"title": "t"},
        "questions": {},
        "model": "decision-model-preview",
    }


def test_decide_retries_a_429() -> None:
    client, requests = _bailian(
        [httpx.Response(429, text="slow down"), httpx.Response(200, json={"answers": {}})]
    )
    assert client.decide({}) == {"answers": {}}
    assert len(requests) == 2


def test_decide_does_not_retry_a_401() -> None:
    client, requests = _bailian([httpx.Response(401, text="bad key")])
    with pytest.raises(BailianError, match="401"):
        client.decide({})
    assert len(requests) == 1


def test_decide_gives_up_after_three_attempts() -> None:
    client, requests = _bailian([httpx.Response(503, text="unavailable")])
    with pytest.raises(BailianError, match="gave up after 3 attempts.*503"):
        client.decide({})
    assert len(requests) == 3


def test_decide_rejects_a_non_object_body() -> None:
    client, _ = _bailian([httpx.Response(200, json=[1, 2, 3])])
    with pytest.raises(BailianError, match="not a JSON object"):
        client.decide({})


def test_missing_key_is_reported() -> None:
    settings = make_settings(bailian={"api_key": None}).bailian
    with pytest.raises(BailianError, match="API_KEY"):
        Bailian(settings)


class FakeCompletions:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class FakeOpenAI:
    def __init__(self, content: str = " ok ") -> None:
        self.completions = FakeCompletions(content)
        self.chat = SimpleNamespace(completions=self.completions)

    def close(self) -> None:
        pass


def test_chat_sends_model_and_thinking_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeOpenAI(" A description. ")
    monkeypatch.setattr(bailian, "OpenAI", lambda **_kwargs: fake)

    with Bailian(make_settings().bailian) as client:
        assert client.chat(system="s", user="u", max_tokens=8) == "A description."

    call = fake.completions.calls[0]
    assert call["model"] == "qwen3.8-flash"
    assert call["extra_body"] == {"enable_thinking": False}
    assert call["messages"][0] == {"role": "system", "content": "s"}


def test_chat_rejects_an_empty_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bailian, "OpenAI", lambda **_kwargs: FakeOpenAI("   "))

    with Bailian(make_settings().bailian) as client, pytest.raises(BailianError, match="empty"):
        client.chat(system="s", user="u", max_tokens=8)
