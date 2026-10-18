from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from conftest import fixture_text, make_paper, make_scored, make_settings
from paperfeed import full_text
from paperfeed.config import FullTextSettings


def settings(**overrides) -> FullTextSettings:
    return FullTextSettings(**{"max_chars": 10_000, **overrides})


def response(status: int, body: str = "") -> SimpleNamespace:
    return SimpleNamespace(status_code=status, text=body)


class FakeClient:
    """Stands in for httpx.Client so attach() never leaves the process."""

    def __init__(self, handler, **_kwargs) -> None:
        self._handler = handler

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *_exc) -> bool:
        return False

    def get(self, url, **_kwargs) -> SimpleNamespace:
        return self._handler(url)


@pytest.fixture(autouse=True)
def small_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixture page is a few hundred chars; the real stub threshold is 2000."""
    monkeypatch.setattr(full_text, "MIN_USEFUL_CHARS", 100)


def page() -> str:
    return fixture_text("full_text.html")


def test_extraction_keeps_prose_and_drops_the_chrome() -> None:
    text = full_text.extract_text(page())

    assert "Entanglement encodes the correlations" in text
    assert "Scaling of the entropy with subsystem size" in text  # captions are prose too
    assert "Cornell University" not in text  # site header
    assert "arXiv is a free distribution service" not in text  # announcement dialog
    assert "Contents" not in text  # LaTeXML table of contents
    assert "Citation-boilerplate-from-the-reference-list" not in text
    assert "Creative Commons" not in text  # site footer


def test_math_comes_from_alttext_not_mathml_text_nodes() -> None:
    text = full_text.extract_text(page())

    assert r"S_{2}=c\,L-A" in text
    assert "annotation" not in text


def test_extraction_collapses_whitespace() -> None:
    text = full_text.extract_text(page())

    assert "\n" not in text
    assert "  " not in text


def test_fetch_returns_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        return response(200, page())

    monkeypatch.setattr(full_text.httpx, "get", fake_get)

    text = full_text.fetch_text(make_paper(arxiv_id="2410.01234"), settings=settings())

    assert text is not None
    assert "Entanglement encodes the correlations" in text
    assert calls == ["https://arxiv.org/html/2410.01234"]


def test_missing_html_is_not_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(full_text.httpx, "get", lambda *_a, **_k: response(404))
    assert full_text.fetch_text(make_paper(), settings=settings()) is None


def test_transport_error_is_not_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args, **_kwargs):
        raise httpx.ConnectTimeout("too slow")

    monkeypatch.setattr(full_text.httpx, "get", boom)
    assert full_text.fetch_text(make_paper(), settings=settings()) is None


def test_stub_page_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        full_text.httpx, "get", lambda *_a, **_k: response(200, "<article><p>Too short.</p>")
    )
    assert full_text.fetch_text(make_paper(), settings=settings()) is None


def test_long_body_is_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(full_text.httpx, "get", lambda *_a, **_k: response(200, page()))

    text = full_text.fetch_text(make_paper(), settings=settings(max_chars=200))

    assert text is not None
    assert len(text) == 200


def test_attach_preserves_order_and_keeps_gaps(monkeypatch: pytest.MonkeyPatch) -> None:
    papers = [
        make_scored(paper=make_paper(arxiv_id="2410.00001")),
        make_scored(paper=make_paper(arxiv_id="2410.00002")),
        make_scored(paper=make_paper(arxiv_id="2410.00003")),
    ]

    def fake_get(url, **_kwargs):
        if "2410.00002" in url:
            return response(404)
        return response(200, page())

    monkeypatch.setattr(full_text.httpx, "Client", lambda **_kwargs: FakeClient(fake_get))

    sourced = full_text.attach(papers, settings=settings())

    assert [item.paper.arxiv_id for item in sourced] == [
        "2410.00001",
        "2410.00002",
        "2410.00003",
    ]
    assert [item.full_text is not None for item in sourced] == [True, False, True]
    assert sourced[0].score == papers[0].final


def test_attach_disabled_fetches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_args, **_kwargs):
        raise AssertionError("no request should be made")

    monkeypatch.setattr(full_text.httpx, "Client", explode)

    sourced = full_text.attach(
        [make_scored()], settings=make_settings(full_text={"enabled": False}).full_text
    )

    assert [item.full_text for item in sourced] == [None]


def test_attach_with_nothing_kept() -> None:
    assert full_text.attach([], settings=settings()) == []


def test_concurrency_is_bounded_by_the_setting() -> None:
    assert make_settings(full_text={"concurrency": 1}).full_text.concurrency == 1
