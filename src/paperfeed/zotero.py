"""Zotero collection and profile-note loading.

The collection supplies positives (and anchor titles); the note supplies the
rubric. `load_profile` is the only entry point that touches the network.
"""

from __future__ import annotations

import logging
from typing import Any

from pyzotero import Zotero

from .config import ZoteroSettings
from .profile_note import Profile, ProfileError, note_title_from_html, parse_profile
from .text import collapse_whitespace

log = logging.getLogger(__name__)

SAVED_TITLE_LIMIT = 20


def _data(item: dict[str, Any]) -> dict[str, Any]:
    """The item's `data` object, or an empty mapping when it is absent."""
    data = item.get("data")
    return data if isinstance(data, dict) else {}


def split_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a collection listing into (bibliographic items, standalone notes).

    `/collections/<key>/items/top` includes standalone notes and excludes child
    notes, so the split is on itemType, not on "drop the notes".
    """
    bib: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    for item in items:
        data = _data(item)
        if data.get("itemType") == "note":
            notes.append(item)
        else:
            bib.append(item)
    return bib, notes


def saved_titles(
    bib_items: list[dict[str, Any]], limit: int = SAVED_TITLE_LIMIT
) -> tuple[str, ...]:
    """Most recently added titles, newest first — anchors for the ranking prompt."""
    ordered = sorted(
        bib_items,
        key=lambda item: str(_data(item).get("dateAdded", "")),
        reverse=True,
    )
    titles = [collapse_whitespace(str(_data(item).get("title", ""))) for item in ordered]
    kept = [title for title in titles if title][:limit]
    return tuple(kept)


def note_label(note: dict[str, Any]) -> str | None:
    """The note's title as far as a user is concerned: API title, else first heading."""
    data = _data(note)
    return collapse_whitespace(str(data.get("title") or "")) or note_title_from_html(
        str(data.get("note", ""))
    )


def select_note(notes: list[dict[str, Any]], wanted: str) -> dict[str, Any]:
    """Find the profile note by title, falling back to "the only note"."""
    target = collapse_whitespace(wanted).lower()
    labelled = [(note, note_label(note)) for note in notes]
    matches = [note for note, label in labelled if label and label.lower() == target]

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ProfileError(f"{len(matches)} notes are titled {wanted!r}; cannot pick one")

    if len(notes) == 1:
        label = labelled[0][1] or "<untitled>"
        log.warning("no note titled %r; using the collection's only note %r", wanted, label)
        return notes[0]

    found = ", ".join(sorted({label for _, label in labelled if label})) or "none"
    raise ProfileError(f"no note titled {wanted!r} in the collection (found: {found})")


def collection_paths(collections: list[dict[str, Any]]) -> dict[str, str]:
    """Map collection key → full path like 'paperfeed/interests'."""
    data = {str(c["key"]): _data(c) for c in collections}
    memo: dict[str, str] = {}

    def path(key: str, depth: int = 0) -> str:
        if key in memo:
            return memo[key]
        node = data.get(key)
        if node is None or depth > 32:
            return ""
        name = collapse_whitespace(str(node.get("name", "")))
        parent = node.get("parentCollection")
        computed = f"{path(str(parent), depth + 1)}/{name}" if parent else name
        memo[key] = computed.strip("/")
        return memo[key]

    return {key: path(key) for key in data}


def resolve_collection(library: Any, target: str) -> str:
    """Resolve a collection path to its key; fail on zero or several matches."""
    wanted = collapse_whitespace(target).strip("/").lower()
    collections = library.everything(library.collections())
    paths = collection_paths(collections)
    matches = [key for key, path in paths.items() if path.lower() == wanted]

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ProfileError(f"collection path {target!r} matches {len(matches)} collections")

    known = sorted(set(paths.values()))
    sample = "; ".join(known[:10]) or "none"
    raise ProfileError(f"no collection at {target!r} (available: {sample})")


def make_library(settings: ZoteroSettings) -> Any:
    if not settings.user_id:
        raise ProfileError("PAPERFEED_ZOTERO__USER_ID is not set")
    if settings.api_key is None:
        raise ProfileError("PAPERFEED_ZOTERO__API_KEY is not set")
    return Zotero(settings.user_id, settings.library_type, settings.api_key.get_secret_value())


def load_profile(settings: ZoteroSettings, library: Any | None = None) -> Profile:
    """Fetch collection + note from Zotero and parse them into a Profile."""
    library = library or make_library(settings)
    key = resolve_collection(library, settings.collection)
    items = library.everything(library.collection_items_top(key))
    bib, notes = split_items(items)
    if not bib:
        log.warning(
            "collection %r has no bibliographic items; ranking has no anchors", settings.collection
        )
    note = select_note(notes, settings.note_title)
    profile = parse_profile(
        str(_data(note).get("note", "")),
        saved_titles=saved_titles(bib),
    )
    log.info(
        "profile: %d topics, %d methods, %d avoid, %d saved titles, language=%r",
        len(profile.topics),
        len(profile.methods),
        len(profile.avoid),
        len(profile.saved_titles),
        profile.language_or("<default>"),
    )
    return profile
