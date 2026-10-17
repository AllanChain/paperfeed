"""arXiv candidates from the per-category RSS feeds.

Each item carries `arxiv:announce_type`; feedparser exposes it as
`entry.arxiv_announce_type`. Values seen in the wild: `new`, `cross`, `replace`,
`replace-cross`. A cross-listed paper appears in several category feeds, so the
dedupe key is the arXiv id with the version suffix stripped.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import feedparser
import httpx

from . import USER_AGENT
from .config import ArxivSettings
from .errors import PaperfeedError
from .text import collapse_whitespace

log = logging.getLogger(__name__)

RSS_URL = "https://export.arxiv.org/rss/{category}"
ARXIV_URL = "https://arxiv.org/abs/{arxiv_id}"
ANNOUNCE_RE = re.compile(r"Announce Type:\s*([a-z-]+)", re.IGNORECASE)
VERSION_RE = re.compile(r"v\d+$")
RSS_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class ArxivError(PaperfeedError):
    """An arXiv RSS feed could not be fetched."""


@dataclass(frozen=True)
class Paper:
    arxiv_id: str
    title: str
    authors: tuple[str, ...]
    abstract: str
    url: str
    categories: tuple[str, ...]
    announce_type: str


def abstract_from_summary(summary: str) -> str:
    """RSS descriptions look like 'arXiv:2401.00001v1 Announce Type: new \\nAbstract: …'."""
    text = summary or ""
    if "Abstract:" in text:
        text = text.split("Abstract:", 1)[1]
    else:
        # No abstract at all: strip "arXiv:2410.01234v1 Announce Type: new" so an
        # announcement without one is dropped rather than ranked on its id.
        text = re.sub(r"arXiv:\S+", "", text)
        text = ANNOUNCE_RE.sub("", text)
    return collapse_whitespace(text)


def arxiv_id_of(*candidates: str) -> str:
    """Extract a bare arXiv id ('2401.00001' or 'cond-mat/0601001') from a URL or OAI id."""
    for value in candidates:
        if not value:
            continue
        match = re.search(r"arxiv\.org/(?:abs|pdf)/([^\s?#]+)", value)
        if match:
            raw = match.group(1)
        else:
            # OAI ids look like 'oai:arXiv.org:cond-mat/0601001'. A bare id can
            # itself contain an archive prefix, so only strip a path from URLs.
            prefixed = value.lower().startswith(("oai:", "arxiv:"))
            raw = value.rsplit(":", 1)[-1] if prefixed else value
            if "://" in raw:
                raw = raw.rstrip("/").rsplit("/", 1)[-1]
        raw = raw.strip("/").removesuffix(".pdf")
        return VERSION_RE.sub("", raw)
    return ""


def _announce_type(entry: dict[str, Any], summary: str) -> str:
    value = str(entry.get("arxiv_announce_type") or "").strip()
    if value:
        return value
    match = ANNOUNCE_RE.search(summary or "")
    return match.group(1).lower() if match else ""


def parse_feed(content: bytes, *, category: str, announce_types: list[str]) -> list[Paper]:
    """Parse one RSS document into papers, dropping non-configured announce types."""
    feed = feedparser.parse(content)
    if feed.get("bozo") and not feed.get("entries"):
        log.warning("%s: unparseable RSS (%s)", category, feed.get("bozo_exception"))
        return []

    allowed = {value.lower() for value in announce_types}
    papers: dict[str, Paper] = {}

    for entry in feed.get("entries", []):
        summary = str(entry.get("summary") or "")
        announce_type = _announce_type(entry, summary)
        if announce_type and announce_type.lower() not in allowed:
            continue
        if not announce_type:
            log.warning("%s: entry has no announce type; keeping it", category)

        arxiv_id = arxiv_id_of(str(entry.get("link") or ""), str(entry.get("id") or ""))
        title = collapse_whitespace(str(entry.get("title") or ""))
        abstract = abstract_from_summary(summary)
        if not arxiv_id or not title or not abstract:
            log.debug("%s: skipping incomplete entry %r", category, title or arxiv_id)
            continue

        authors = tuple(
            name
            for author in str(entry.get("author") or "").split(",")
            if (name := collapse_whitespace(author))
        )
        tags = tuple(str(tag.get("term", "")) for tag in entry.get("tags", []) if tag.get("term"))
        categories = tags or (category,)

        papers.setdefault(
            arxiv_id,
            Paper(
                arxiv_id=arxiv_id,
                title=title,
                authors=authors,
                abstract=abstract,
                url=ARXIV_URL.format(arxiv_id=arxiv_id),
                categories=categories,
                announce_type=announce_type or "unknown",
            ),
        )

    log.info("%s: %d candidate(s)", category, len(papers))
    return list(papers.values())


def fetch(settings: ArxivSettings, *, client: httpx.Client | None = None) -> list[Paper]:
    """Fetch every configured category, dedupe by arXiv id, cap the candidate count."""
    own_client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=RSS_TIMEOUT)
    seen: dict[str, Paper] = {}
    try:
        for category in settings.categories:
            try:
                response = own_client.get(RSS_URL.format(category=category))
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ArxivError(f"fetching {category}: {exc}") from exc
            for paper in parse_feed(
                response.content, category=category, announce_types=settings.announce_types
            ):
                seen.setdefault(paper.arxiv_id, paper)
    finally:
        if client is None:
            own_client.close()

    papers = list(seen.values())
    dropped = max(0, len(papers) - settings.max_candidates)
    if dropped:
        log.info("capping candidates at %d (−%d)", settings.max_candidates, dropped)
    return papers[: settings.max_candidates]
