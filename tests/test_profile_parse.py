from __future__ import annotations

import pytest

from conftest import fixture_text
from paperfeed.profile_note import (
    ProfileError,
    note_title_from_html,
    parse_profile,
)
from paperfeed.zotero import (
    collection_paths,
    resolve_collection,
    saved_titles,
    select_note,
    split_items,
)

NOTE_HTML = fixture_text("note.html")


def test_parses_every_section() -> None:
    profile = parse_profile(NOTE_HTML)
    assert "nonequilibrium dynamics in quantum materials" in profile.focus
    assert profile.topics == (
        "tensor networks",
        "MPS and PEPS",
        "quantum Monte Carlo",
        "strongly correlated electrons & magnetism",
    )
    assert profile.methods == ("DMRG", "QMC", "dynamical mean-field theory")
    assert profile.avoid == (
        "experimental synthesis",
        "device engineering",
        "purely classical ML",
    )
    assert "One paragraph, concrete, no hype." in profile.style
    assert profile.language == "English"


def test_nested_paragraph_inside_list_item_is_not_duplicated() -> None:
    profile = parse_profile(NOTE_HTML)
    assert profile.methods.count("DMRG") == 1


def test_nested_paragraph_inside_list_item_does_not_duplicate_focus() -> None:
    profile = parse_profile(
        "<h2>Focus</h2><ul><li><p>computational physics</p></li></ul><h2>Style</h2><p>terse</p>"
    )
    assert profile.focus == "computational physics"


def test_nested_list_keeps_parent_and_child_separate() -> None:
    profile = parse_profile(NOTE_HTML)
    assert "tensor networks MPS and PEPS" not in profile.topics
    assert "MPS and PEPS" in profile.topics


def test_headings_are_the_title() -> None:
    assert note_title_from_html(NOTE_HTML) == "paperfeed profile"


def test_missing_focus_fails_loudly() -> None:
    with pytest.raises(ProfileError, match="Focus"):
        parse_profile("<h2>Topics</h2><ul><li>x</li></ul><h2>Style</h2><p>be terse</p>")


def test_missing_style_fails_loudly() -> None:
    with pytest.raises(ProfileError, match="Style"):
        parse_profile("<h2>Focus</h2><p>something</p>")


def test_unknown_headings_do_not_leak_into_sections() -> None:
    profile = parse_profile(
        "<h2>Focus</h2><p>computational physics</p>"
        "<h2>References</h2><p>ignore me</p>"
        "<h2>Style</h2><p>terse</p>"
    )
    assert profile.focus == "computational physics"
    assert "ignore me" not in profile.style
    assert profile.topics == ()


def test_language_falls_back_when_absent() -> None:
    profile = parse_profile("<h2>Focus</h2><p>f</p><h2>Style</h2><p>s</p>")
    assert profile.language == ""
    assert profile.language_or("English") == "English"


def _note(key: str, html: str, title: str | None = None) -> dict:
    data: dict = {"itemType": "note", "note": html, "dateAdded": "2026-09-01T00:00:00Z"}
    if title is not None:
        data["title"] = title
    return {"key": key, "data": data}


def test_select_note_uses_first_heading_when_api_has_no_title() -> None:
    notes = [_note("A", NOTE_HTML), _note("B", "<h1>other</h1>")]
    assert select_note(notes, "paperfeed profile")["key"] == "A"


def test_select_note_prefers_api_title_when_present() -> None:
    notes = [_note("A", "<h1>not the title</h1>", title="paperfeed profile")]
    assert select_note(notes, "paperfeed profile")["key"] == "A"


def test_select_note_falls_back_to_the_only_note() -> None:
    notes = [_note("A", "<h1>something else</h1>")]
    assert select_note(notes, "paperfeed profile")["key"] == "A"


def test_select_note_fails_when_ambiguous() -> None:
    notes = [_note("A", "<h1>one</h1>"), _note("B", "<h1>two</h1>")]
    with pytest.raises(ProfileError, match="no note titled"):
        select_note(notes, "paperfeed profile")


def test_split_items_and_saved_titles() -> None:
    items = [
        {
            "key": "1",
            "data": {"itemType": "journalArticle", "title": "older", "dateAdded": "2026-01-01"},
        },
        {"key": "2", "data": {"itemType": "preprint", "title": "newer", "dateAdded": "2026-09-01"}},
        {"key": "3", "data": {"itemType": "note", "note": "<h1>x</h1>"}},
        {
            "key": "4",
            "data": {"itemType": "journalArticle", "title": "", "dateAdded": "2026-08-01"},
        },
    ]
    bib, notes = split_items(items)
    assert [item["key"] for item in bib] == ["1", "2", "4"]
    assert [note["key"] for note in notes] == ["3"]
    assert saved_titles(bib) == ("newer", "older")
    assert saved_titles(bib, limit=1) == ("newer",)


def _collection(key: str, name: str, parent: str | bool = False) -> dict:
    return {"key": key, "data": {"name": name, "parentCollection": parent}}


class FakeLibrary:
    def __init__(self, collections: list[dict]) -> None:
        self._collections = collections

    def collections(self) -> list[dict]:
        return self._collections

    def everything(self, items: list[dict]) -> list[dict]:
        return items


def test_collection_paths_walk_parents() -> None:
    paths = collection_paths(
        [
            _collection("ROOT", "paperfeed"),
            _collection("CHILD", "interests", "ROOT"),
            _collection("OTHER", "unrelated"),
        ]
    )
    assert paths["CHILD"] == "paperfeed/interests"
    assert paths["OTHER"] == "unrelated"


def test_resolve_collection_by_full_path() -> None:
    library = FakeLibrary(
        [
            _collection("ROOT", "paperfeed"),
            _collection("CHILD", "interests", "ROOT"),
            _collection("OTHER", "interests"),
        ]
    )
    assert resolve_collection(library, "paperfeed/interests") == "CHILD"


def test_resolve_collection_fails_on_duplicate_paths() -> None:
    library = FakeLibrary(
        [
            _collection("R1", "paperfeed"),
            _collection("C1", "interests", "R1"),
            _collection("R2", "paperfeed"),
            _collection("C2", "interests", "R2"),
        ]
    )
    with pytest.raises(ProfileError, match="matches 2 collections"):
        resolve_collection(library, "paperfeed/interests")


def test_resolve_collection_fails_when_absent() -> None:
    library = FakeLibrary([_collection("ROOT", "paperfeed")])
    with pytest.raises(ProfileError, match="no collection at"):
        resolve_collection(library, "paperfeed/interests")


def test_list_sections_accept_plain_paragraphs() -> None:
    profile = parse_profile(
        "<h2>Focus</h2><p>f</p>"
        "<h2>Topics</h2><p>tensor networks</p><p>quantum Monte Carlo</p>"
        "<h2>Style</h2><p>s</p>"
    )
    assert profile.topics == ("tensor networks", "quantum Monte Carlo")
