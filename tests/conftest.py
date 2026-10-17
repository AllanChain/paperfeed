"""Shared test helpers and recorded fixtures. No test touches the network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from paperfeed.arxiv_source import Paper
from paperfeed.config import Settings
from paperfeed.profile_note import Profile, parse_profile
from paperfeed.rank import ScoredPaper

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def fixture_json(name: str) -> dict[str, Any]:
    return json.loads(fixture_text(name))


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "zotero": {
            "user_id": "1234567",
            "api_key": "zotero-key",
            "collection": "paperfeed/interests",
            "note_title": "paperfeed profile",
        },
        "bailian": {"api_key": "bailian-key"},
        "feishu": {"webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/test"},
    }
    for section, values in overrides.items():
        if isinstance(values, dict) and isinstance(base.get(section), dict):
            base[section] = {**base[section], **values}
        else:
            base[section] = values
    return Settings(_env_file=None, **base)


def make_paper(**overrides: Any) -> Paper:
    fields: dict[str, Any] = {
        "arxiv_id": "2410.01234",
        "title": "Entanglement scaling in a two-dimensional Fermi gas",
        "authors": ("Ana Author", "Bo Author"),
        "abstract": "We compute the second Renyi entropy with a tensor network.",
        "url": "https://arxiv.org/abs/2410.01234",
        "categories": ("quant-ph",),
        "announce_type": "new",
    }
    fields.update(overrides)
    return Paper(**fields)


def make_scored(final: float = 0.81, **overrides: Any) -> ScoredPaper:
    fields: dict[str, Any] = {
        "paper": make_paper(),
        "relevance": 0.85,
        "avoid": 0.08,
        "final": final,
    }
    fields.update(overrides)
    return ScoredPaper(**fields)


@pytest.fixture
def profile() -> Profile:
    return parse_profile(fixture_text("note.html"), saved_titles=("Saved paper one",))
