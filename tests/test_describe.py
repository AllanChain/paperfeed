from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from conftest import make_paper, make_sourced
from paperfeed.describe import (
    build_system_prompt,
    build_user_prompt,
    clean,
    describe_all,
)
from paperfeed.profile_note import Profile


def _bailian(chat):
    """The one method describe.py needs from a Bailian."""
    return SimpleNamespace(chat=chat)


def test_system_prompt_carries_the_profile(profile: Profile) -> None:
    prompt = build_system_prompt(profile, "English")
    assert profile.focus in prompt
    assert "tensor networks" in prompt
    assert "DMRG" in prompt
    assert "experimental synthesis" in prompt
    assert "One paragraph" in prompt
    assert "Write in English" in prompt


def test_language_falls_back_when_note_omits_it(profile: Profile) -> None:
    prompt = build_system_prompt(replace(profile, language=""), "Chinese")
    assert "Write in Chinese" in prompt


def test_user_prompt_has_title_and_abstract(profile: Profile) -> None:
    prompt = build_user_prompt(make_paper())
    assert "Title: Entanglement scaling" in prompt
    assert "Abstract: We compute the second Renyi entropy" in prompt
    assert "Full text:" not in prompt


def test_user_prompt_carries_the_body_when_there_is_one() -> None:
    prompt = build_user_prompt(make_paper(), "Body prose that the abstract lacks.")
    assert "Abstract: We compute the second Renyi entropy" in prompt
    assert prompt.endswith("Full text:\nBody prose that the abstract lacks.")


def test_full_text_prompt_asks_for_the_actual_result(profile: Profile) -> None:
    abstract_only = build_system_prompt(profile, "English")
    with_body = build_system_prompt(profile, "English", full_text=True)

    assert "full text" not in abstract_only
    assert "full text" in with_body
    assert "actual finding" in with_body
    # Nothing else about the contract changes: drop the inserted lines and the
    # prompt is byte-identical to the abstract-only one.
    assert "60-90 words" in with_body
    assert profile.focus in with_body
    inserted = with_body[with_body.index("You are given") : with_body.index("Never mention")]
    assert with_body.replace(inserted, "") == abstract_only


def test_clean_strips_quotes_markdown_and_whitespace() -> None:
    assert clean('  "A crisp   summary."  ') == "A crisp summary."
    assert clean("- bullet-ish") == "bullet-ish"
    assert clean("\u201cquoted\u201d") == "quoted"


def test_describe_all_drops_failed_papers(profile: Profile) -> None:
    def fake_chat(*, system, user, max_tokens):
        if "bad paper" in user:
            raise RuntimeError("upstream refused")
        return '"A crisp summary."'

    scored = [
        make_sourced(final=0.81, paper=make_paper(arxiv_id="2410.00001", title="good paper one")),
        make_sourced(final=0.72, paper=make_paper(arxiv_id="2410.00002", title="bad paper")),
        make_sourced(final=0.66, paper=make_paper(arxiv_id="2410.00003", title="good paper two")),
    ]

    described = describe_all(
        profile,
        scored,
        bailian=_bailian(fake_chat),
        fallback_language="English",
        concurrency=2,
    )

    assert [item.paper.title for item in described] == ["good paper one", "good paper two"]
    assert described[0].description == "A crisp summary."
    assert described[0].score == pytest.approx(0.81)


def test_describe_all_picks_the_prompt_per_paper(profile: Profile) -> None:
    seen: dict[str, str] = {}

    def fake_chat(*, system, user, max_tokens):
        seen[user.split("\n")[0]] = system
        return "A crisp summary."

    describe_all(
        profile,
        [
            make_sourced(
                paper=make_paper(arxiv_id="2410.00001", title="with body"), full_text="x" * 10
            ),
            make_sourced(paper=make_paper(arxiv_id="2410.00002", title="abstract only")),
        ],
        bailian=_bailian(fake_chat),
        fallback_language="English",
        concurrency=2,
    )

    assert "full text" in seen["Title: with body"]
    assert "full text" not in seen["Title: abstract only"]


def test_describe_all_with_nothing_kept() -> None:
    described = describe_all(
        object(),  # never touched
        [],
        bailian=object(),  # never touched
        fallback_language="English",
        concurrency=1,
    )
    assert described == []


def test_description_budget_and_audience_rules(profile: Profile) -> None:
    prompt = build_system_prompt(profile, "English")
    assert "60-90 words" in prompt
    assert "Do not address the reader" in prompt
    assert "Never mention what they do not care about" in prompt
