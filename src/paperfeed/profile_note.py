"""The reader's profile note: HTML into a validated Profile.

The note supplies the rubric — Focus, Topics, Methods, Avoid, Style, Language.
Anything unparseable is a hard error: a silently generic feed is worse than a
failed run.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal

from bs4 import BeautifulSoup
from bs4.element import NavigableString

from .errors import PaperfeedError
from .text import collapse_whitespace

HEADING_TAGS = ("h1", "h2", "h3")
BLOCK_TAGS = (*HEADING_TAGS, "p", "li")
SECTIONS = ("focus", "topics", "methods", "avoid", "style", "language")
# List sections collect one entry per block; the rest concatenate paragraphs.
LIST_SECTIONS = frozenset({"topics", "methods", "avoid"})


class ProfileError(PaperfeedError):
    """The collection or note cannot be used as a profile."""


@dataclass(frozen=True)
class Profile:
    focus: str
    topics: tuple[str, ...]
    methods: tuple[str, ...]
    avoid: tuple[str, ...]
    style: str
    language: str
    saved_titles: tuple[str, ...] = ()

    def language_or(self, fallback: str) -> str:
        return self.language or fallback


@dataclass(frozen=True)
class _Block:
    kind: Literal["heading", "paragraph", "item"]
    text: str


def _own_text(element: Any) -> str:
    """Text belonging to this element, excluding a nested list's own items."""
    parts: list[str] = []
    for child in element.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif getattr(child, "name", None) in ("ul", "ol"):
            continue
        else:
            parts.append(child.get_text(" ", strip=True))
    return collapse_whitespace(" ".join(parts))


def _blocks(soup: BeautifulSoup) -> Iterator[_Block]:
    """Walk block elements in document order, skipping non-list blocks nested in a block."""
    for element in soup.find_all(BLOCK_TAGS):
        # A <p> inside an <li> is already part of that item's text (see _own_text),
        # so emitting it again would duplicate the text. A nested <li> is its own
        # item, not a duplicate, and is kept.
        if element.name != "li" and element.find_parent(BLOCK_TAGS) is not None:
            continue
        text = _own_text(element) if element.name == "li" else element.get_text(" ", strip=True)
        text = collapse_whitespace(text)
        if not text:
            continue
        if element.name in HEADING_TAGS:
            yield _Block("heading", text)
        elif element.name == "li":
            yield _Block("item", text)
        else:
            yield _Block("paragraph", text)


def _section_of(heading: str) -> str | None:
    key = heading.strip().strip(":").strip().lower()
    return key if key in SECTIONS else None


def note_title_from_html(note_html: str) -> str | None:
    """First heading of a note — what Zotero's UI shows as the note's title."""
    for block in _blocks(BeautifulSoup(note_html, "html.parser")):
        if block.kind == "heading":
            return block.text
    return None


def parse_profile(note_html: str, *, saved_titles: tuple[str, ...] = ()) -> Profile:
    """Parse the note HTML into a validated Profile. Raises if Focus/Style is absent."""
    soup = BeautifulSoup(note_html, "html.parser")

    collected: dict[str, list[str]] = {name: [] for name in SECTIONS}
    section: str | None = None

    for block in _blocks(soup):
        if block.kind == "heading":
            section = _section_of(block.text)
            continue
        if section is None:
            continue
        # Paragraphs count in list sections too: a list written as plain lines
        # should not vanish.
        if section in LIST_SECTIONS:
            _append_unique(collected[section], block.text)
        else:
            collected[section].append(block.text)

    focus = " ".join(collected["focus"]).strip()
    style = " ".join(collected["style"]).strip()
    if not focus:
        raise ProfileError("profile note has no non-empty 'Focus' section")
    if not style:
        raise ProfileError("profile note has no non-empty 'Style' section")

    return Profile(
        focus=focus,
        topics=tuple(collected["topics"]),
        methods=tuple(collected["methods"]),
        avoid=tuple(collected["avoid"]),
        style=style,
        language=" ".join(collected["language"]).strip(),
        saved_titles=saved_titles,
    )


def _append_unique(bucket: list[str], value: str) -> None:
    if value not in bucket:
        bucket.append(value)
