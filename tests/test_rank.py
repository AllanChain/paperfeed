from __future__ import annotations

from types import SimpleNamespace

import pytest

from conftest import fixture_json, make_paper
from paperfeed.profile_note import Profile
from paperfeed.rank import (
    AVOID_CRITERIA,
    LEVEL_COUNT,
    RELEVANCE_LEVELS,
    avoid_from,
    build_payload,
    final_score,
    rank,
    relevance_from,
    score_paper,
    select_top,
)


def _bailian(decide):
    """The one method rank.py needs from a Bailian."""
    return SimpleNamespace(decide=decide)


def test_payload_shape(profile: Profile) -> None:
    payload = build_payload(profile, make_paper())

    assert "model" not in payload  # the client injects its own model
    assert payload["state"]["title"].startswith("Entanglement scaling")
    assert payload["state"]["categories"] == ["quant-ph"]

    relevance = payload["questions"]["relevance"]
    assert relevance["type"] == "score"
    assert len(relevance["criteria"]) == LEVEL_COUNT == 4
    assert profile.focus in relevance["instructions"]
    assert "tensor networks" in relevance["instructions"]
    assert "Saved paper one" in relevance["instructions"]

    avoid = payload["questions"]["avoid"]
    assert avoid["type"] == "noul"
    assert avoid["criteria"] == AVOID_CRITERIA
    assert "experimental synthesis" in avoid["instructions"]


def test_relevance_is_expected_level_normalized(profile: Profile) -> None:
    assert relevance_from({"score": 2.25}) == pytest.approx(0.75)
    assert relevance_from({"score": 0}) == pytest.approx(0.0)
    assert relevance_from({"score": 3}) == pytest.approx(1.0)


def test_out_of_range_answers_are_clamped() -> None:
    assert relevance_from({"score": 9.0}) == 1.0
    assert relevance_from({"score": -1.0}) == 0.0
    assert avoid_from({"noul": 1.4}) == 1.0


def test_final_score_applies_penalty_and_clips() -> None:
    assert final_score(0.75, 0.99, 0.5) == pytest.approx(0.255)
    assert final_score(0.1, 0.99, 1.0) == 0.0
    assert final_score(1.0, 0.0, 0.5) == 1.0


def test_score_paper_reads_recorded_response(profile: Profile) -> None:
    response = fixture_json("systemone_response.json")
    scored = score_paper(
        profile,
        make_paper(),
        bailian=_bailian(lambda _payload: response),
        avoid_penalty=0.5,
    )
    assert scored.relevance == pytest.approx(0.75)
    assert scored.avoid == pytest.approx(0.04)
    assert scored.final == pytest.approx(0.73)


def test_failed_call_scores_zero(profile: Profile) -> None:
    def boom(_payload):
        raise TimeoutError("upstream is down")

    scored = score_paper(profile, make_paper(), bailian=_bailian(boom), avoid_penalty=0.5)
    assert scored.final == 0.0
    assert scored.relevance == 0.0


def test_malformed_answer_scores_zero(profile: Profile) -> None:
    broken = {"answers": {"relevance": {"score": 2.0}}}
    scored = score_paper(
        profile,
        make_paper(),
        bailian=_bailian(lambda _payload: broken),
        avoid_penalty=0.5,
    )
    assert scored.final == 0.0


def test_rank_orders_by_final_score(profile: Profile) -> None:
    papers = [make_paper(arxiv_id=f"2410.0000{i}", title=f"paper {i}") for i in range(4)]
    finals = {"paper 0": 0.2, "paper 1": 0.9, "paper 2": 0.5, "paper 3": 0.9}

    def decide(payload):
        final = finals[payload["state"]["title"]]
        return {
            "answers": {"relevance": {"score": final * (LEVEL_COUNT - 1)}, "avoid": {"noul": 0.0}}
        }

    scored = rank(
        profile,
        papers,
        bailian=_bailian(decide),
        avoid_penalty=0.5,
        concurrency=2,
    )
    assert [item.final for item in scored] == pytest.approx([0.9, 0.9, 0.5, 0.2])
    assert [item.paper.arxiv_id for item in scored[:2]] == ["2410.00001", "2410.00003"]


def test_select_top_filters_then_caps(profile: Profile) -> None:
    papers = [make_paper(arxiv_id=f"2410.0000{i}", title=f"paper {i}") for i in range(4)]
    finals = {"paper 0": 0.9, "paper 1": 0.5, "paper 2": 0.4, "paper 3": 0.1}

    def decide(payload):
        final = finals[payload["state"]["title"]]
        return {
            "answers": {"relevance": {"score": final * (LEVEL_COUNT - 1)}, "avoid": {"noul": 0.0}}
        }

    scored = rank(
        profile,
        papers,
        bailian=_bailian(decide),
        avoid_penalty=0.5,
        concurrency=2,
    )
    kept = select_top(scored, min_score=0.35, max_papers=10)
    assert [item.paper.title for item in kept] == ["paper 0", "paper 1", "paper 2"]
    assert len(select_top(scored, min_score=0.35, max_papers=1)) == 1
    assert select_top(scored, min_score=0.95, max_papers=10) == []


def test_instructions_forbid_category_as_evidence(profile: Profile) -> None:
    instructions = build_payload(profile, make_paper())["questions"]["relevance"]["instructions"]
    assert "Judge only what the abstract states" in instructions
    assert "not by itself evidence of relevance" in instructions
    assert "names the system" not in instructions


def test_levels_require_a_named_topic_and_method() -> None:
    text = " ".join(RELEVANCE_LEVELS)
    assert "reader's Topics" in text
    assert "vocabulary, not subject matter" in text
