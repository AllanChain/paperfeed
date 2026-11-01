from __future__ import annotations

import logging

import httpx
import pytest

from conftest import fixture_bytes, make_settings
from paperfeed import arxiv_source
from paperfeed.arxiv_source import arxiv_id_of, fetch, iter_listings, parse_listing

QUANT_PH = fixture_bytes("list_quant_ph.html")
COND_MAT = fixture_bytes("list_cond_mat.html")


def test_keeps_new_and_cross_but_not_replacements() -> None:
    papers = parse_listing(QUANT_PH, category="quant-ph")
    assert [paper.announce_type for paper in papers] == ["new", "new", "cross", "cross"]
    assert "2311.03977" not in {paper.arxiv_id for paper in papers}


def test_fields_come_from_the_listing_row() -> None:
    papers = {paper.arxiv_id: paper for paper in parse_listing(QUANT_PH, category="quant-ph")}
    paper = papers["2610.37745"]

    assert paper.title == "Defining 2-group symmetries on lattices and extracting their anomalies"
    assert paper.abstract.startswith("Ordinary $0$-form symmetries $G$")
    assert "Announce Type" not in paper.abstract
    assert paper.authors == ("Yitao Feng", "Yu-An Chen")
    assert paper.categories == ("quant-ph", "cond-mat.str-el", "math-ph", "math.QA")
    assert paper.url == "https://arxiv.org/abs/2610.37745"


def test_math_and_entities_survive() -> None:
    html = QUANT_PH.replace(b"Magic in Grover's Algorithm", b"Magic &amp; Grover&#x27;s Algorithm")
    papers = {paper.arxiv_id: paper for paper in parse_listing(html, category="quant-ph")}
    assert papers["2610.37745"].title.startswith("Defining 2-group")
    assert papers["2610.37772"].title == (
        "Magic & Grover's Algorithm and the Role of Oracle Synthesis"
    )
    assert "$0$-form" in papers["2610.37745"].abstract


def test_replacement_section_is_ignored() -> None:
    # The fixture has one replacement entry; it never reaches the parser's output.
    assert b"Replacement submissions" in QUANT_PH
    papers = parse_listing(QUANT_PH, category="quant-ph")
    assert len(papers) == 4


def test_entry_without_abstract_is_dropped() -> None:
    papers = parse_listing(COND_MAT, category="cond-mat.str-el")
    assert "2610.37761" not in {paper.arxiv_id for paper in papers}


def test_truncated_section_warns(caplog: pytest.LogCaptureFixture) -> None:
    html = QUANT_PH.replace(
        b"New submissions (showing 2 of 2 entries)",
        b"New submissions (showing 1 of 5000 entries)",
    )
    with caplog.at_level(logging.WARNING):
        papers = parse_listing(html, category="quant-ph")

    assert len(papers) == 4
    assert "shows 1 of 5000 entries" in caplog.text


def test_arxiv_id_handles_old_style_and_relative_urls() -> None:
    assert arxiv_id_of("https://arxiv.org/abs/cond-mat/0601001v2") == "cond-mat/0601001"
    assert arxiv_id_of("https://arxiv.org/pdf/2410.01234v3.pdf") == "2410.01234"
    assert arxiv_id_of("/abs/2410.01234") == "2410.01234"
    assert arxiv_id_of("cond-mat/0601001v2") == "cond-mat/0601001"
    assert arxiv_id_of("oai:arXiv.org:quant-ph/0601001") == "quant-ph/0601001"
    assert arxiv_id_of("https://arxiv.org/abs/2410.01234/") == "2410.01234"


def _client(mapping: dict[str, bytes]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        category = request.url.path.split("/list/", 1)[1].split("/", 1)[0]
        return httpx.Response(200, content=mapping[category])

    return httpx.Client(transport=httpx.MockTransport(handler))


def _settings(**arxiv: object):
    return make_settings(arxiv={"categories": ["quant-ph", "cond-mat.str-el"], **arxiv})


def test_fetch_dedupes_cross_listed_papers() -> None:
    settings = _settings(max_candidates=100)
    client = _client({"quant-ph": QUANT_PH, "cond-mat.str-el": COND_MAT})

    papers = fetch(settings.arxiv, client=client, delay=0)

    ids = [paper.arxiv_id for paper in papers]
    assert ids == ["2610.37745", "2610.37772", "2610.37765", "2610.37793", "2610.37754"]
    assert ids.count("2610.37745") == 1


def test_fetch_caps_each_category_separately() -> None:
    settings = _settings(max_candidates=1)
    client = _client({"quant-ph": QUANT_PH, "cond-mat.str-el": COND_MAT})

    papers = fetch(settings.arxiv, client=client, delay=0)

    assert [paper.arxiv_id for paper in papers] == ["2610.37745", "2610.37754"]


def test_iter_listings_yields_one_page_at_a_time() -> None:
    settings = _settings(max_candidates=100)
    client = _client({"quant-ph": QUANT_PH, "cond-mat.str-el": COND_MAT})

    pages = list(iter_listings(settings.arxiv, client=client, delay=0))

    assert [len(page) for page in pages] == [4, 2]


def test_crawl_delay_is_measured_from_the_previous_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []
    monkeypatch.setattr(arxiv_source.time, "sleep", lambda seconds: slept.append(seconds))
    settings = _settings(max_candidates=100)
    client = _client({"quant-ph": QUANT_PH, "cond-mat.str-el": COND_MAT})

    list(iter_listings(settings.arxiv, client=client, delay=15))

    assert len(slept) == 1
    assert 0 < slept[0] <= 15
