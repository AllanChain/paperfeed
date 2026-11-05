"""arXiv candidates from the per-category HTML listing pages.

`/list/<category>/new` carries three sections — new submissions, cross
submissions and replacements — each in its own `<dl id="articles">` with the
section name in its `<h3>`. An entry is a `<dt>`/`<dd>` pair whose abstract is
already in the page (`<p class="mathjax">`), so no RSS feed or per-paper `/abs`
fetch is involved. Only new and cross sections are kept.

A cross-listed paper appears on several category pages, so the dedupe key is the
arXiv id with the version suffix stripped.

arxiv.org/robots.txt asks for a 15 s crawl delay on `/list`. The delay is
measured from the previous response, and ranking runs between pages, so most of
the wait is already spent by the time the next page is due. That delay assumes
this run is the only client on its IP; a shared or cloud host can still be
refused with 429, so a listing fetch is retried with a rate-limit-sized backoff
before the run gives up.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup, Tag

from . import USER_AGENT, net
from .config import ArxivSettings
from .errors import PaperfeedError
from .text import collapse_whitespace

log = logging.getLogger(__name__)

LIST_URL = "https://arxiv.org/list/{category}/new?show=2000"
ARXIV_URL = "https://arxiv.org/abs/{arxiv_id}"
CRAWL_DELAY = 15.0
# First retry wait for a refused listing: a per-IP limit needs longer than the
# 1 s an API retry uses, and the next attempt doubles this (jittered, max 4x).
RETRY_WAIT = 30.0
FETCH_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

VERSION_RE = re.compile(r"v\d+$")
SECTION_COUNT_RE = re.compile(r"\(showing\s+(\d+)\s+of\s+(\d+)\s+entries?\)", re.IGNORECASE)
SUBJECT_RE = re.compile(r"\(([a-z][a-z-]*(?:\.[A-Za-z0-9-]+)?)\)")

ANNOUNCE_NEW = "new"
ANNOUNCE_CROSS = "cross"
ANNOUNCE_REPLACE = "replace"


class ArxivError(PaperfeedError):
    """An arXiv listing page could not be fetched."""


@dataclass(frozen=True)
class Paper:
    arxiv_id: str
    title: str
    authors: tuple[str, ...]
    abstract: str
    url: str
    categories: tuple[str, ...]
    announce_type: str


def arxiv_id_of(*candidates: str) -> str:
    """Extract a bare arXiv id ('2401.00001' or 'cond-mat/0601001') from a URL or OAI id."""
    for value in candidates:
        if not value:
            continue
        match = re.search(r"(?:arxiv\.org)?/(?:abs|pdf)/([^\s?#]+)", value)
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


def _section_type(heading: str) -> str:
    lowered = heading.lower()
    if "new submission" in lowered:
        return ANNOUNCE_NEW
    if "cross" in lowered:
        return ANNOUNCE_CROSS
    if "replacement" in lowered:
        return ANNOUNCE_REPLACE
    return ""


def _entries(dl: Tag) -> Iterator[tuple[Tag, Tag]]:
    """Yield the page's `<dt>`/`<dd>` pairs in document order."""
    pending: Tag | None = None
    for child in dl.children:
        if not isinstance(child, Tag):
            continue
        if child.name == "dt":
            pending = child
        elif child.name == "dd" and pending is not None:
            yield pending, child
            pending = None


def _labelled(div: Tag, label: str) -> str:
    """Read a listing field such as `<span>Title:</span> …` without the label."""
    text = collapse_whitespace(div.get_text(" ", strip=True))
    prefix = f"{label}:"
    return text[len(prefix) :].strip() if text.startswith(prefix) else text


def _subjects(dd: Tag) -> tuple[str, ...]:
    div = dd.find("div", class_="list-subjects")
    if div is None:
        return ()
    text = _labelled(div, "Subjects")
    return tuple(dict.fromkeys(SUBJECT_RE.findall(text)))


def _authors(dd: Tag) -> tuple[str, ...]:
    # The links are one author each, so a comma inside a name cannot split it.
    links = dd.select(".list-authors a")
    if links:
        names = (collapse_whitespace(link.get_text(" ", strip=True)) for link in links)
        return tuple(name for name in names if name)
    div = dd.find("div", class_="list-authors")
    if div is None:
        return ()
    return tuple(
        name for part in _labelled(div, "Authors").split(",") if (name := collapse_whitespace(part))
    )


def _parse_entry(dt: Tag, dd: Tag, *, category: str, announce_type: str) -> Paper | None:
    link = dt.find("a", href=re.compile(r"/abs/"))
    arxiv_id = arxiv_id_of(str(link["href"])) if link else ""
    title_div = dd.find("div", class_="list-title")
    title = _labelled(title_div, "Title") if title_div else ""
    abstract_p = dd.find("p", class_="mathjax")
    abstract = collapse_whitespace(abstract_p.get_text(" ", strip=True)) if abstract_p else ""
    if not arxiv_id or not title or not abstract:
        log.debug("%s: skipping incomplete entry %r", category, title or arxiv_id)
        return None

    return Paper(
        arxiv_id=arxiv_id,
        title=title,
        authors=_authors(dd),
        abstract=abstract,
        url=ARXIV_URL.format(arxiv_id=arxiv_id),
        categories=_subjects(dd) or (category,),
        announce_type=announce_type,
    )


def parse_listing(content: bytes | str, *, category: str) -> list[Paper]:
    """Parse one listing page, keeping new and cross entries."""
    soup = BeautifulSoup(content, "html.parser")

    papers: list[Paper] = []
    for dl in soup.find_all("dl", id="articles"):
        heading_tag = dl.find("h3")
        heading = collapse_whitespace(heading_tag.get_text(" ", strip=True)) if heading_tag else ""
        announce_type = _section_type(heading)
        if announce_type not in (ANNOUNCE_NEW, ANNOUNCE_CROSS):
            continue

        shown = SECTION_COUNT_RE.search(heading)
        if shown and int(shown.group(1)) < int(shown.group(2)):
            log.warning(
                "%s: %r shows %s of %s entries; the rest are off the page",
                category,
                heading,
                shown.group(1),
                shown.group(2),
            )

        for dt, dd in _entries(dl):
            paper = _parse_entry(dt, dd, category=category, announce_type=announce_type)
            if paper is not None:
                papers.append(paper)

    log.info("%s: %d candidate(s)", category, len(papers))
    return papers


def iter_listings(
    settings: ArxivSettings,
    *,
    client: httpx.Client | None = None,
    delay: float = CRAWL_DELAY,
) -> Iterator[list[Paper]]:
    """Yield one category's papers at a time, spacing requests by `delay` seconds.

    The caller can rank a page while the crawl delay for the next one elapses:
    the delay is measured from the previous response, not from the previous yield.
    A transient refusal (429, 5xx, network) is retried before `ArxivError` is raised.
    """
    last_response: float | None = None
    with net.open_client(
        headers={"User-Agent": USER_AGENT}, timeout=FETCH_TIMEOUT, reuse=client
    ) as http:
        for category in settings.categories:
            if last_response is not None and delay > 0:
                remaining = delay - (time.monotonic() - last_response)
                if remaining > 0:
                    log.debug("%s: waiting %.1fs for the crawl delay", category, remaining)
                    time.sleep(remaining)

            try:
                response = net.get(
                    http,
                    LIST_URL.format(category=category),
                    wait_seconds=RETRY_WAIT,
                )
                response.raise_for_status()
            except (net.Transient, httpx.HTTPError) as exc:
                raise ArxivError(f"fetching {category}: {exc}") from exc
            last_response = time.monotonic()

            papers = parse_listing(response.content, category=category)
            if len(papers) > settings.max_candidates:
                log.info(
                    "%s: capping candidates at %d (−%d)",
                    category,
                    settings.max_candidates,
                    len(papers) - settings.max_candidates,
                )
                papers = papers[: settings.max_candidates]
            yield papers


def fetch(
    settings: ArxivSettings,
    *,
    client: httpx.Client | None = None,
    delay: float = CRAWL_DELAY,
) -> list[Paper]:
    """Fetch every configured category, dedupe by arXiv id."""
    seen: dict[str, Paper] = {}
    for papers in iter_listings(settings, client=client, delay=delay):
        for paper in papers:
            seen.setdefault(paper.arxiv_id, paper)
    return list(seen.values())
