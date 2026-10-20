"""Full text for the papers that actually reach the card.

arXiv renders LaTeX submissions to HTML with LaTeXML, so the body arrives already
flattened: macros expanded, `\\input` inlined, floats and the bibliography marked
up as elements. That is why this fetches HTML rather than the e-print source —
measured on the same papers, the source is 1.2-4.3x longer, and stripping it
needs tar/zip/gzip detection plus `$`-pairing and float-matching regexes that
silently delete prose when they desync.

Only the description reads the full text. Ranking stays abstract-only: 300
candidates times a whole paper is not affordable, and the decision model returns
calibrated numbers, not prose.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from bs4 import BeautifulSoup

from . import USER_AGENT, net
from .arxiv_source import Paper
from .concurrency import map_bounded
from .config import FullTextSettings
from .rank import ScoredPaper
from .text import collapse_whitespace

log = logging.getLogger(__name__)

HTML_URL = "https://arxiv.org/html/{arxiv_id}"
# LaTeXML's names for the parts a reader skips. Everything left under <article>
# is prose, captions and appendices.
DROP_CLASS = re.compile(r"ltx_(?:TOC|bibliography)", re.IGNORECASE)
DROP_TAGS = ("script", "style", "nav", "footer", "header")
# Below this, the page is a stub or a conversion failure, not a paper.
MIN_USEFUL_CHARS = 2000


@dataclass(frozen=True)
class Sourced:
    """A scored paper and the body text fetched for it, if any."""

    scored: ScoredPaper
    full_text: str | None = None

    @property
    def paper(self) -> Paper:
        return self.scored.paper


def _alttext(tag: Any) -> str:
    """LaTeXML's MathML carries the original TeX in `alttext`.

    MathML's own text nodes duplicate each expression across the presentation and
    content trees, so reading them inflates the body and yields garbled math.
    """
    value = tag.get("alttext")
    if isinstance(value, str):
        return value
    return " ".join(str(part) for part in value) if value else ""


def extract_text(html: str) -> str:
    """Body prose from an arXiv HTML page, with site chrome and references removed."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(DROP_TAGS):
        tag.decompose()

    root = soup.find("article") or soup.find("main") or soup.body or soup
    for element in root.find_all(class_=DROP_CLASS):
        element.decompose()
    for math in root.find_all("math"):
        math.replace_with(f" {_alttext(math)} ")
    return collapse_whitespace(root.get_text(" ", strip=True))


def fetch_text(
    paper: Paper, *, settings: FullTextSettings, client: httpx.Client | None = None
) -> str | None:
    """Best-effort body text. Every failure is a missing body, never a failed run."""
    url = HTML_URL.format(arxiv_id=paper.arxiv_id)
    try:
        with net.open_client(
            headers={"User-Agent": USER_AGENT}, timeout=settings.timeout, reuse=client
        ) as http:
            response = http.get(url, follow_redirects=True)
    except httpx.HTTPError as exc:
        log.info(
            "%s: no full text (%s: %s); describing the abstract",
            paper.arxiv_id,
            type(exc).__name__,
            exc,
        )
        return None

    if response.status_code != 200:
        # Not an error: arXiv has HTML only for LaTeX submissions it converted
        # successfully, so roughly one in six papers has none.
        log.info(
            "%s: no full text (HTTP %d); describing the abstract",
            paper.arxiv_id,
            response.status_code,
        )
        return None

    text = extract_text(response.text)
    if len(text) < MIN_USEFUL_CHARS:
        log.info(
            "%s: full text is only %d chars; describing the abstract", paper.arxiv_id, len(text)
        )
        return None
    if len(text) > settings.max_chars:
        log.warning(
            "%s: full text is %d chars, truncated to %d",
            paper.arxiv_id,
            len(text),
            settings.max_chars,
        )
        text = text[: settings.max_chars]
    return text


def attach(scored: Sequence[ScoredPaper], *, settings: FullTextSettings) -> list[Sourced]:
    """Fetch the body for every paper that will be described. Order is preserved."""
    if not scored:
        return []
    if not settings.enabled:
        log.info("full text disabled; describing abstracts only")
        return [Sourced(item) for item in scored]

    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=settings.timeout) as client:
        texts = map_bounded(
            lambda item: fetch_text(item.paper, settings=settings, client=client),
            scored,
            limit=settings.concurrency,
        )

    found = sum(1 for text in texts if text is not None)
    log.info("full text: %d of %d paper(s)", found, len(scored))
    return [Sourced(item, text) for item, text in zip(scored, texts, strict=True)]
