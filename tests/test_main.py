from __future__ import annotations

import json

import pytest

from conftest import fixture_text, make_paper, make_scored, make_settings
from paperfeed import main
from paperfeed.config import Settings
from paperfeed.describe import Described
from paperfeed.doctor import CheckResult
from paperfeed.full_text import Sourced
from paperfeed.profile_note import ProfileError, parse_profile


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Replace every side effect of run(): Zotero, arXiv, ranking and models."""
    settings = make_settings()
    paper = make_paper(arxiv_id="2410.00001", title="paper a")
    scored = make_scored(paper=paper, final=0.8)
    described = Described(scored=scored, description="A crisp summary.")
    sent: list[tuple[str, dict]] = []

    monkeypatch.setattr(main, "Settings", lambda: settings)
    monkeypatch.setattr(
        main, "load_profile", lambda _zotero: parse_profile(fixture_text("note.html"))
    )
    monkeypatch.setattr(main.arxiv_source, "fetch", lambda _arxiv: [paper])
    monkeypatch.setattr(main.rank, "rank", lambda *_a, **_k: [scored])
    monkeypatch.setattr(
        main.full_text, "attach", lambda scored, **_k: [Sourced(item) for item in scored]
    )
    monkeypatch.setattr(main.describe, "describe_all", lambda *_a, **_k: [described])
    monkeypatch.setattr(main.feishu, "send", lambda url, message: sent.append((url, message)))

    return {"settings": settings, "sent": sent, "paper": paper}


def test_dry_run_prints_the_card_and_sends_nothing(wired, capsys) -> None:
    assert main.run(["--dry-run"]) == 0
    card = json.loads(capsys.readouterr().out)

    assert card["msg_type"] == "interactive"
    assert card["card"]["header"]["title"]["content"].startswith("paperfeed · ")
    assert "paper a" in json.dumps(card)
    assert wired["sent"] == []


def test_real_run_posts_the_card(wired) -> None:
    assert main.run([]) == 0
    assert len(wired["sent"]) == 1
    url, message = wired["sent"][0]
    assert url == wired["settings"].feishu.webhook_url.get_secret_value()
    assert message["msg_type"] == "interactive"


def test_nothing_above_threshold_sends_nothing(wired, monkeypatch, capsys) -> None:
    weak = make_scored(paper=wired["paper"], final=0.1)
    monkeypatch.setattr(main.rank, "rank", lambda *_a, **_k: [weak])

    assert main.run([]) == 0
    assert wired["sent"] == []
    assert capsys.readouterr().out == ""


def test_failed_descriptions_send_nothing(wired, monkeypatch) -> None:
    monkeypatch.setattr(main.describe, "describe_all", lambda *_a, **_k: [])
    assert main.run([]) == 0
    assert wired["sent"] == []


def test_every_kept_paper_gets_a_full_text_fetch(wired, monkeypatch) -> None:
    kept = [
        make_scored(paper=make_paper(arxiv_id=f"2410.0000{n}", title=f"paper {n}"), final=0.7)
        for n in (1, 2, 3)
    ]
    monkeypatch.setattr(main.rank, "rank", lambda *_a, **_k: kept)
    seen: list[list[str]] = []

    def attach(scored, **_kwargs):
        seen.append([item.paper.arxiv_id for item in scored])
        return [Sourced(item, "body text") for item in scored]

    monkeypatch.setattr(main.full_text, "attach", attach)

    assert main.run(["--dry-run"]) == 0
    assert seen == [["2410.00001", "2410.00002", "2410.00003"]]


def test_low_scoring_papers_get_no_full_text(wired, monkeypatch) -> None:
    weak = make_scored(paper=wired["paper"], final=0.1)
    monkeypatch.setattr(main.rank, "rank", lambda *_a, **_k: [weak])
    seen: list[list] = []
    monkeypatch.setattr(main.full_text, "attach", lambda scored, **_k: seen.append(scored) or [])

    assert main.run([]) == 0
    assert seen == []


def test_missing_settings_exit_code(monkeypatch, caplog) -> None:
    monkeypatch.setattr(main, "Settings", lambda: Settings(_env_file=None))
    assert main.run(["--dry-run"]) == 2
    assert "PAPERFEED_BAILIAN__API_KEY" in caplog.text


def test_invalid_config_exit_code(monkeypatch, caplog) -> None:
    monkeypatch.setenv("PAPERFEED_ARXIV__CATEGORIES", "not-json")
    monkeypatch.setattr(main, "Settings", lambda: Settings(_env_file=None))

    assert main.run(["--dry-run"]) == 2
    assert "invalid configuration" in caplog.text


def test_dry_run_does_not_require_webhook(wired, monkeypatch, capsys) -> None:
    settings = make_settings(feishu={"webhook_url": None})
    monkeypatch.setattr(main, "Settings", lambda: settings)

    assert main.run(["--dry-run"]) == 0
    assert "paper a" in capsys.readouterr().out
    assert wired["sent"] == []


def test_missing_webhook_fails_before_any_work(wired, monkeypatch, caplog) -> None:
    settings = make_settings(feishu={"webhook_url": None})
    monkeypatch.setattr(main, "Settings", lambda: settings)
    fetched: list[int] = []
    monkeypatch.setattr(main.arxiv_source, "fetch", lambda _arxiv: fetched.append(1) or [])

    assert main.run([]) == 2
    assert "PAPERFEED_FEISHU__WEBHOOK_URL" in caplog.text
    assert fetched == []
    assert wired["sent"] == []


def test_reportable_failure_returns_one(wired, monkeypatch, caplog) -> None:
    def broken(_zotero):
        raise ProfileError("profile is broken")

    monkeypatch.setattr(main, "load_profile", broken)

    assert main.run([]) == 1
    assert "profile is broken" in caplog.text


def test_check_reports_failures(monkeypatch, capsys) -> None:
    monkeypatch.setattr(main, "Settings", lambda: make_settings())
    monkeypatch.setattr(
        main.doctor, "_decision_check", lambda _settings: CheckResult("decision", True, "ok")
    )
    monkeypatch.setattr(
        main.doctor, "_chat_check", lambda _settings: CheckResult("chat", False, "nope")
    )

    def broken(_zotero):
        raise ProfileError("no note")

    monkeypatch.setattr(main.doctor, "load_profile", broken)

    assert main.run(["--check"]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "no note" in out


def test_check_reports_success(monkeypatch, capsys) -> None:
    monkeypatch.setattr(main, "Settings", lambda: make_settings())
    monkeypatch.setattr(
        main.doctor,
        "check",
        lambda _settings: [CheckResult("chat", True, "reply='ok'")],
    )

    assert main.run(["--check"]) == 0
    assert "OK" in capsys.readouterr().out
