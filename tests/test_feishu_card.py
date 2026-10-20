from __future__ import annotations

import httpx
import pytest

from conftest import make_paper
from paperfeed import feishu
from paperfeed.bailian import Usage
from paperfeed.describe import Described


def described(**overrides) -> Described:
    paper = make_paper(
        title=overrides.pop("title", "Entanglement scaling in a two-dimensional gas"),
        authors=overrides.pop("authors", ("Ana Author", "Bo Author")),
    )
    return Described(
        paper=paper,
        score=overrides.pop("final", 0.81),
        description=overrides.pop("description", "A crisp summary."),
    )


def test_card_layout() -> None:
    card = feishu.build_card(
        date="2026-10-17",
        collected=12,
        kept=[described(), described(final=0.62)],
        categories=["quant-ph", "cond-mat.str-el"],
        color="green",
    )

    assert card["config"] == {"wide_screen_mode": True}
    assert card["header"]["template"] == "green"
    assert card["header"]["title"] == {"tag": "plain_text", "content": "paperfeed · 2026-10-17"}

    intro = card["elements"][0]["text"]["content"]
    assert "**2**" in intro
    assert "12 candidates" in intro
    assert "quant-ph / cond-mat.str-el" in intro

    dividers = [element for element in card["elements"] if element["tag"] == "hr"]
    assert len(dividers) == 2  # one before each paper, and nothing after the last

    bodies = [element for element in card["elements"] if element["tag"] == "div"][1:]
    assert len(bodies) == 2
    assert "**[Entanglement scaling in a two-dimensional gas]" in bodies[0]["text"]["content"]
    assert "(https://arxiv.org/abs/2410.01234)" in bodies[0]["text"]["content"]
    assert "score 0.81" in bodies[0]["text"]["content"]
    assert "score 0.62" in bodies[1]["text"]["content"]
    assert card["elements"][-1]["tag"] == "div"
    assert not [element for element in card["elements"] if element["tag"] == "note"]


def test_authors_are_truncated() -> None:
    card = feishu.build_card(
        date="2026-10-17",
        collected=1,
        kept=[described(authors=("A", "B", "C", "D", "E"))],
        categories=["quant-ph"],
    )
    content = card["elements"][2]["text"]["content"]
    assert "A, B, C et al." in content


def test_double_asterisks_cannot_bold_the_rest_of_the_card() -> None:
    card = feishu.build_card(
        date="2026-10-17",
        collected=1,
        kept=[described(description="They measure **susceptibility** carefully.")],
        categories=["quant-ph"],
    )
    content = card["elements"][2]["text"]["content"]
    assert "**" not in content.split("score 0.81</font>\n\n")[1]


def test_brackets_in_titles_cannot_break_the_link() -> None:
    card = feishu.build_card(
        date="2026-10-17",
        collected=1,
        kept=[described(title="A [very] odd title")],
        categories=["quant-ph"],
    )
    content = card["elements"][2]["text"]["content"]
    assert "[A (very) odd title](https://arxiv.org/abs/2410.01234)" in content


def test_token_usage_is_a_trailing_foot_note() -> None:
    usage = Usage()
    usage.rank.input_tokens = 183_400
    usage.describe.input_tokens = 55_100
    usage.describe.output_tokens = 9_000

    card = feishu.build_card(
        date="2026-10-17",
        collected=300,
        kept=[described()],
        categories=["quant-ph"],
        usage=usage,
    )

    assert card["elements"][-1] == {
        "tag": "note",
        "elements": [
            {"tag": "plain_text", "content": "rank 183.4k in · describe 55.1k in / 9k out"}
        ],
    }


def test_zero_usage_adds_no_foot_note() -> None:
    card = feishu.build_card(
        date="2026-10-17",
        collected=1,
        kept=[described()],
        categories=["quant-ph"],
        usage=Usage(),
    )

    assert not [element for element in card["elements"] if element["tag"] == "note"]


def test_token_counts_stay_compact() -> None:
    assert feishu._compact_tokens(612) == "612"
    assert feishu._compact_tokens(9_000) == "9k"
    assert feishu._compact_tokens(9_123) == "9.1k"
    assert feishu._compact_tokens(1_200_000) == "1.2M"


def test_message_wrapper() -> None:
    card = {"config": {}, "header": {}, "elements": []}
    assert feishu.build_message(card) == {
        "msg_type": "interactive",
        "card": card,
    }


def _client(responses: list) -> tuple[httpx.Client, list[int]]:
    state = {"calls": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        return responses[min(state["calls"] - 1, len(responses) - 1)]

    return httpx.Client(transport=httpx.MockTransport(handler)), state


def test_send_accepts_code_zero() -> None:
    client, state = _client([httpx.Response(200, json={"code": 0, "msg": "success"})])
    feishu.send("https://example.invalid/hook", {"msg_type": "interactive"}, client=client)
    assert state["calls"] == 1


def test_send_rejects_non_zero_code_despite_http_200() -> None:
    client, _ = _client([httpx.Response(200, json={"code": 19021, "msg": "sign match fail"})])
    with pytest.raises(feishu.FeishuError, match="19021"):
        feishu.send("https://example.invalid/hook", {"msg_type": "interactive"}, client=client)


def test_send_does_not_retry_a_server_error() -> None:
    client, state = _client(
        [httpx.Response(500, text="boom"), httpx.Response(200, json={"code": 0})]
    )
    with pytest.raises(feishu.FeishuError, match="HTTP 500"):
        feishu.send("https://example.invalid/hook", {"msg_type": "interactive"}, client=client)
    assert state["calls"] == 1


def test_send_does_not_retry_a_network_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(feishu.FeishuError, match="ConnectError"):
        feishu.send("https://example.invalid/hook", {"msg_type": "interactive"}, client=client)
