"""Descriptions: one paragraph per kept paper, written for this reader.

The system prompt is the profile note's Focus + Style, not a generic
"summarize this paper" instruction — that is the whole point of the note. The
decision model cannot do this job: its max output is 0 tokens.

Papers that arrive with `full_text` get a slightly different prompt: the same
contract, plus an instruction to name the actual result instead of restating the
abstract, which is only honest when the body is in context.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from openai import OpenAI

from . import bailian
from .arxiv_source import Paper
from .concurrency import map_bounded
from .config import BailianSettings
from .full_text import Sourced
from .profile_note import Profile
from .rank import ScoredPaper
from .text import collapse_whitespace

log = logging.getLogger(__name__)

MAX_ABSTRACT_CHARS = 4000
MAX_TOKENS = 300


@dataclass(frozen=True)
class Described:
    scored: ScoredPaper
    description: str

    @property
    def paper(self) -> Paper:
        return self.scored.paper

    @property
    def score(self) -> float:
        return self.scored.final


def build_system_prompt(
    profile: Profile, fallback_language: str, *, full_text: bool = False
) -> str:
    language = profile.language_or(fallback_language)
    lines = [
        "You write reading notes for one specific researcher, for their daily arXiv feed.",
        "",
        f"Reader's focus: {profile.focus}",
    ]
    if profile.topics:
        lines.append("Topics they track: " + "; ".join(profile.topics))
    if profile.methods:
        lines.append("Methods they use: " + "; ".join(profile.methods))
    if profile.avoid:
        lines.append("What they do not care about: " + "; ".join(profile.avoid))
    lines += [
        "",
        "What they asked for in a description: " + profile.style,
        "",
        f"Write in {language}. One paragraph, 60-90 words, plain text. No bullets, no",
        "headings, no markdown, no quotes, no 'This paper...' opener. State what is",
        "computed or measured and why it matters to this reader. Concrete and specific:",
        "name the system, the method, the observable. No hype, no 'novel'/'groundbreaking'.",
        "Do not address the reader: never write 'For researchers…', 'For those…', 'you',",
        "or a closing sentence about who benefits.",
    ]
    if full_text:
        lines.append(
            "You are given the paper's full text, not just its abstract. Use it: name the"
            " actual finding — what was computed or measured, in what regime, and what came"
            " out — rather than restating the abstract."
        )
    if profile.avoid:
        lines.append(
            "Never mention what they do not care about — not even to say the paper avoids it."
        )
    return "\n".join(lines)


def build_user_prompt(paper: Paper, full_text: str | None = None) -> str:
    authors = ", ".join(paper.authors[:6]) or "unknown"
    categories = ", ".join(paper.categories)
    prompt = (
        f"Title: {paper.title}\n"
        f"Authors: {authors}\n"
        f"Categories: {categories}\n\n"
        f"Abstract: {paper.abstract[:MAX_ABSTRACT_CHARS]}"
    )
    if full_text:
        return f"{prompt}\n\nFull text:\n{full_text}"
    return prompt


def clean(text: str) -> str:
    """Collapse whitespace and strip the markdown/quotes a model likes to add."""
    text = collapse_whitespace(text).strip("\"'\u201c\u201d\u2018\u2019")
    if text.startswith(("#", "-", "*")):
        text = text.lstrip("#-* ").strip()
    return text


def describe(
    sourced: Sourced,
    *,
    settings: BailianSettings,
    system: str,
    client: OpenAI | None = None,
) -> Described:
    """One description call. Raises on failure; the caller decides whether to drop."""
    text = bailian.chat(
        settings,
        system=system,
        user=build_user_prompt(sourced.paper, sourced.full_text),
        max_tokens=MAX_TOKENS,
        client=client,
    )
    description = clean(text)
    if not description:
        raise bailian.BailianError("description was empty after cleaning")
    return Described(scored=sourced.scored, description=description)


def describe_all(
    profile: Profile,
    sources: Sequence[Sourced],
    *,
    settings: BailianSettings,
    fallback_language: str,
) -> list[Described]:
    """Describe every kept paper concurrently, dropping (and logging) failures."""
    if not sources:
        return []

    # Two prompts, not one per paper: the full-text variant only adds one clause.
    systems = {
        False: build_system_prompt(profile, fallback_language),
        True: build_system_prompt(profile, fallback_language, full_text=True),
    }
    client = bailian.openai_client(settings)

    def one(item: Sourced) -> Described | None:
        try:
            return describe(
                item,
                settings=settings,
                system=systems[item.full_text is not None],
                client=client,
            )
        except Exception as exc:  # noqa: BLE001 - one bad description drops one paper
            log.error(
                "description for %s failed (%s: %s); dropping it",
                item.paper.arxiv_id,
                type(exc).__name__,
                exc,
            )
            return None

    try:
        results = map_bounded(one, sources, limit=settings.chat_concurrency)
    finally:
        client.close()

    return [item for item in results if item is not None]
