"""Ranking by System One: one paper per call, two questions.

`relevance` is a `score` question over four fixed levels; the API returns the
*expected* level index, which normalizes to [0, 1] by dividing by level_count − 1.
`avoid` is a `noul` question returning P(yes) directly. Both are calibrated
numbers, so no response parsing or prompt-and-pray scaling is involved.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from .arxiv_source import Paper
from .bailian import decide
from .concurrency import map_bounded
from .config import BailianSettings
from .profile_note import Profile

DecideFn = Callable[[BailianSettings, dict[str, Any], httpx.Client | None], dict[str, Any]]

log = logging.getLogger(__name__)

RELEVANCE_LEVELS: tuple[str, ...] = (
    "Unrelated: outside the reader's Focus, sharing neither a Topic nor a Method with "
    "the profile (for example experimental synthesis, device engineering, purely "
    "classical ML).",
    "Adjacent: same broad field or one shared Method, but the abstract's actual question "
    "is none of the reader's Topics — the overlap is vocabulary, not subject matter.",
    "Relevant: the abstract explicitly works on at least one of the reader's Topics and "
    "uses at least one of their Methods; useful, but not central to the reader's work.",
    "Core: a central Focus topic attacked with a central Method; likely to change how "
    "the reader works, or something they would cite.",
)
LEVEL_COUNT = len(RELEVANCE_LEVELS)

AVOID_CRITERIA = {
    "true": "The paper is mainly one of the Avoid items (for example an experimental "
    "synthesis, device-engineering, or purely classical-ML paper).",
    "false": "The paper is not mainly an Avoid item; any overlap is incidental.",
}

MAX_INSTRUCTION_CHARS = 4000
MAX_ANCHOR_CHARS = 160


class ScoreAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    score: float


class NoulAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    noul: float


@dataclass(frozen=True)
class ScoredPaper:
    paper: Paper
    relevance: float
    avoid: float
    final: float


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def relevance_instructions(profile: Profile) -> str:
    parts = [f"Focus: {profile.focus}"]
    if profile.topics:
        parts.append("Topics: " + "; ".join(profile.topics))
    if profile.methods:
        parts.append("Methods: " + "; ".join(profile.methods))
    if profile.saved_titles:
        anchors = "; ".join(title[:MAX_ANCHOR_CHARS] for title in profile.saved_titles)
        parts.append(f"Papers this reader already saved: {anchors}")
    parts.append(
        "Judge only what the abstract states. Sharing an arXiv category with the profile "
        "is not by itself evidence of relevance."
    )
    text = "\n".join(parts)
    return text[:MAX_INSTRUCTION_CHARS]


def avoid_instructions(profile: Profile) -> str:
    if not profile.avoid:
        return "The reader listed nothing to avoid; answer false unless the paper is trivial."
    return "Topics this reader does not want: " + "; ".join(profile.avoid)


def build_payload(profile: Profile, paper: Paper, model: str) -> dict[str, Any]:
    return {
        "model": model,
        "state": {
            "title": paper.title,
            "abstract": paper.abstract,
            "categories": list(paper.categories),
        },
        "questions": {
            "relevance": {
                "type": "score",
                "instructions": relevance_instructions(profile),
                "criteria": list(RELEVANCE_LEVELS),
            },
            "avoid": {
                "type": "noul",
                "instructions": avoid_instructions(profile),
                "criteria": AVOID_CRITERIA,
            },
        },
    }


def relevance_from(answer: dict[str, Any]) -> float:
    """score is the expected level index, already interpolated between levels."""
    return clamp(ScoreAnswer.model_validate(answer).score / (LEVEL_COUNT - 1))


def avoid_from(answer: dict[str, Any]) -> float:
    return clamp(NoulAnswer.model_validate(answer).noul)


def final_score(relevance: float, avoid: float, penalty: float) -> float:
    return clamp(max(0.0, relevance - penalty * avoid))


def score_paper(
    profile: Profile,
    paper: Paper,
    *,
    settings: BailianSettings,
    avoid_penalty: float,
    decide_fn: DecideFn = decide,
    client: httpx.Client | None = None,
) -> ScoredPaper:
    """Score one paper. Any failure scores 0.0 — never invent a judgement."""
    payload = build_payload(profile, paper, settings.decision_model)
    try:
        response = decide_fn(settings, payload, client)
        answers = response["answers"]
        relevance = relevance_from(answers["relevance"])
        avoid = avoid_from(answers["avoid"])
    except Exception as exc:  # noqa: BLE001 - a bad answer is a failed paper, not a failed run
        log.warning(
            "scoring %s failed (%s: %s); scoring 0.0", paper.arxiv_id, type(exc).__name__, exc
        )
        return ScoredPaper(paper=paper, relevance=0.0, avoid=0.0, final=0.0)

    final = final_score(relevance, avoid, avoid_penalty)
    log.debug(
        "%s relevance=%.2f avoid=%.2f final=%.2f",
        paper.arxiv_id,
        relevance,
        avoid,
        final,
    )
    return ScoredPaper(paper=paper, relevance=relevance, avoid=avoid, final=final)


def rank(
    profile: Profile,
    papers: list[Paper],
    *,
    settings: BailianSettings,
    avoid_penalty: float,
    decide_fn: DecideFn = decide,
) -> list[ScoredPaper]:
    """Score every candidate concurrently; return sorted best-first."""
    if not papers:
        return []

    with httpx.Client() as client:
        worker = partial(
            score_paper,
            profile,
            settings=settings,
            avoid_penalty=avoid_penalty,
            decide_fn=decide_fn,
            client=client,
        )
        scored = map_bounded(worker, papers, limit=settings.decision_concurrency)

    scored.sort(key=lambda item: (-item.final, -item.relevance, item.paper.arxiv_id))
    return scored


def select_top(
    scored: list[ScoredPaper], *, min_score: float, max_papers: int
) -> list[ScoredPaper]:
    kept = [item for item in scored if item.final >= min_score]
    return kept[:max_papers]
