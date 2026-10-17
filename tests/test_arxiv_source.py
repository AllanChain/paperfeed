from __future__ import annotations

import httpx

from conftest import fixture_bytes, make_settings
from paperfeed.arxiv_source import (
    abstract_from_summary,
    arxiv_id_of,
    fetch,
    parse_feed,
)

QUANT_PH = fixture_bytes("rss_quant_ph.xml")
COND_MAT = fixture_bytes("rss_cond_mat.xml")


def test_only_configured_announce_types_survive() -> None:
    papers = parse_feed(QUANT_PH, category="quant-ph", announce_types=["new", "cross"])
    assert [paper.arxiv_id for paper in papers] == ["2410.01234", "2410.05678"]


def test_replace_cross_is_excluded_by_default() -> None:
    papers = parse_feed(QUANT_PH, category="quant-ph", announce_types=["new", "cross"])
    assert "2410.11111" not in {paper.arxiv_id for paper in papers}


def test_replace_is_kept_when_configured() -> None:
    papers = parse_feed(QUANT_PH, category="quant-ph", announce_types=["new", "cross", "replace"])
    assert "2410.09012" in {paper.arxiv_id for paper in papers}
    assert "2410.11111" not in {paper.arxiv_id for paper in papers}


def test_abstract_prefix_and_entities_are_cleaned() -> None:
    papers = {
        paper.arxiv_id: paper
        for paper in parse_feed(QUANT_PH, category="quant-ph", announce_types=["new", "cross"])
    }
    paper = papers["2410.01234"]
    assert paper.abstract.startswith("We compute the second Renyi entropy")
    assert "Announce Type" not in paper.abstract
    assert "arXiv:" not in paper.abstract
    assert "&" in paper.title
    assert paper.announce_type == "new"
    assert paper.authors == ("Ana Author", "Bo Author", "Cy Author", "Di Author")
    assert paper.url == "https://arxiv.org/abs/2410.01234"


def test_version_suffix_is_stripped() -> None:
    papers = {
        paper.arxiv_id: paper
        for paper in parse_feed(QUANT_PH, category="quant-ph", announce_types=["new", "cross"])
    }
    assert "2410.05678" in papers
    assert papers["2410.05678"].url == "https://arxiv.org/abs/2410.05678"


def test_entry_without_abstract_is_dropped() -> None:
    papers = parse_feed(COND_MAT, category="cond-mat.str-el", announce_types=["new", "cross"])
    assert "2410.33333" not in {paper.arxiv_id for paper in papers}


def test_abstract_from_summary_without_prefix() -> None:
    assert abstract_from_summary("Just  an\n abstract.") == "Just an abstract."


def test_arxiv_id_handles_old_style_and_pdf_urls() -> None:
    assert arxiv_id_of("https://arxiv.org/abs/cond-mat/0601001v2") == "cond-mat/0601001"
    assert arxiv_id_of("https://arxiv.org/pdf/2410.01234v3.pdf") == "2410.01234"
    assert arxiv_id_of("cond-mat/0601001v2") == "cond-mat/0601001"
    assert arxiv_id_of("oai:arXiv.org:quant-ph/0601001") == "quant-ph/0601001"
    assert arxiv_id_of("https://arxiv.org/abs/2410.01234/") == "2410.01234"


def _client(mapping: dict[str, bytes]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        category = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, content=mapping[category])

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_dedupes_cross_listed_papers() -> None:
    settings = make_settings(
        arxiv={"categories": ["quant-ph", "cond-mat.str-el"], "max_candidates": 100}
    )
    client = _client({"quant-ph": QUANT_PH, "cond-mat.str-el": COND_MAT})
    papers = fetch(settings.arxiv, client=client)
    ids = [paper.arxiv_id for paper in papers]
    assert ids == ["2410.01234", "2410.05678", "2410.22222"]
    assert ids.count("2410.05678") == 1


def test_fetch_respects_max_candidates() -> None:
    settings = make_settings(
        arxiv={"categories": ["quant-ph", "cond-mat.str-el"], "max_candidates": 1}
    )
    client = _client({"quant-ph": QUANT_PH, "cond-mat.str-el": COND_MAT})
    papers = fetch(settings.arxiv, client=client)
    assert [paper.arxiv_id for paper in papers] == ["2410.01234"]
