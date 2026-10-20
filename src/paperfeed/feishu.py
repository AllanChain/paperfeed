"""Feishu delivery: one styled `interactive` card per day.

One-way by design — the card is the whole interface. A `200` response with a
non-zero `code`/`StatusCode` in the body is still a failure.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import httpx

from . import USER_AGENT, net
from .describe import Described
from .errors import PaperfeedError
from .mathfmt import to_display
from .text import collapse_whitespace

log = logging.getLogger(__name__)

AUTHOR_LIMIT = 3
TIMEOUT = httpx.Timeout(20.0, connect=10.0)


class FeishuError(PaperfeedError):
    """The webhook rejected the message."""


def _title_text(title: str) -> str:
    """Keep link syntax intact: a stray bracket would break the lark_md link."""
    return to_display(collapse_whitespace(title)).replace("[", "(").replace("]", ")")


def _authors(authors: Sequence[str], limit: int = AUTHOR_LIMIT) -> str:
    named = [name for author in authors if (name := collapse_whitespace(author))]
    if not named:
        return "unknown authors"
    if len(named) <= limit:
        return ", ".join(named)
    return ", ".join(named[:limit]) + " et al."


def _description_text(description: str) -> str:
    # A description containing "**" would bold the rest of the card.
    return collapse_whitespace(to_display(description)).replace("**", "*")


def build_card(
    *,
    date: str,
    collected: int,
    kept: Sequence[Described],
    categories: Sequence[str],
    color: str = "blue",
) -> dict[str, Any]:
    """Build the card body (without the `msg_type` wrapper)."""
    intro = (
        f"**{len(kept)}** papers worth your time · "
        f"{collected} candidates · {' / '.join(categories)}"
    )
    elements: list[dict[str, Any]] = [{"tag": "div", "text": {"tag": "lark_md", "content": intro}}]

    for item in kept:
        content = (
            f"**[{_title_text(item.paper.title)}]({item.paper.url})**\n"
            f"<font color='grey'>{_authors(item.paper.authors)}</font> · "
            f"<font color='blue'>score {item.score:.2f}</font>"
            f"\n\n{_description_text(item.description)}"
        )
        elements.append({"tag": "hr"})
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content}})

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": color,
            "title": {"tag": "plain_text", "content": f"paperfeed · {date}"},
        },
        "elements": elements,
    }


def build_message(card: dict[str, Any]) -> dict[str, Any]:
    return {"msg_type": "interactive", "card": card}


def send(url: str, message: dict[str, Any], *, client: httpx.Client | None = None) -> None:
    """POST the card once. Retrying could deliver the same card twice."""
    with net.open_client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, reuse=client) as http:
        try:
            response = net.post_once(http, url, message)
        except net.Transient as exc:
            raise FeishuError(str(exc)) from exc

    if response.status_code >= 400:
        raise FeishuError(f"HTTP {response.status_code}: {response.text[:300]}")

    try:
        raw = response.json()
    except ValueError:
        raw = {}
    body: dict[str, Any] = raw if isinstance(raw, dict) else {}
    code = body.get("code", body.get("StatusCode", 0))
    if code not in (0, None):
        raise FeishuError(f"webhook code={code} msg={body.get('msg') or body.get('StatusMessage')}")
    log.info("card sent to Feishu")
