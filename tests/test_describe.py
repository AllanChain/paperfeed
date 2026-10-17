from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from conftest import make_paper, make_scored, make_settings
from paperfeed import describe
from paperfeed.describe import (
    build_system_prompt,
    build_user_prompt,
    clean,
    describe_all,
)
from paperfeed.profile_note import Profile


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


def test_clean_strips_quotes_markdown_and_whitespace() -> None:
    assert clean('  "A crisp   summary."  ') == "A crisp summary."
    assert clean("- bullet-ish") == "bullet-ish"
    assert clean("\u201cquoted\u201d") == "quoted"


def test_describe_all_drops_failed_papers(
    monkeypatch: pytest.MonkeyPatch, profile: Profile
) -> None:
    monkeypatch.setattr(
        describe.bailian,
        "openai_client",
        lambda _settings: SimpleNamespace(close=lambda: None),
    )

    def fake_chat(_settings, *, system, user, max_tokens, client=None):
        if "bad paper" in user:
            raise RuntimeError("upstream refused")
        return '"A crisp summary."'

    monkeypatch.setattr(describe.bailian, "chat", fake_chat)

    scored = [
        make_scored(paper=make_paper(arxiv_id="2410.00001", title="good paper one")),
        make_scored(paper=make_paper(arxiv_id="2410.00002", title="bad paper")),
        make_scored(paper=make_paper(arxiv_id="2410.00003", title="good paper two")),
    ]

    described = describe_all(
        profile,
        scored,
        settings=make_settings().bailian,
        fallback_language="English",
    )

    assert [item.paper.title for item in described] == ["good paper one", "good paper two"]
    assert described[0].description == "A crisp summary."


def test_describe_all_with_nothing_kept() -> None:
    described = describe_all(
        object(),  # never touched
        [],
        settings=make_settings().bailian,
        fallback_language="English",
    )
    assert described == []


def test_description_budget_and_audience_rules(profile: Profile) -> None:
    prompt = build_system_prompt(profile, "English")
    assert "60-90 words" in prompt
    assert "Do not address the reader" in prompt
    assert "Never mention what they do not care about" in prompt
