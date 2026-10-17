"""Orchestration and CLI.

`paperfeed`              fetch → rank → describe → send
`paperfeed --dry-run`    the same, printing the card JSON instead of sending
`paperfeed --check`      prove the host, key, model ids and Zotero access work
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from pydantic_settings import SettingsError

from . import arxiv_source, describe, doctor, feishu, rank
from .config import ConfigError, Settings
from .errors import PaperfeedError
from .zotero import load_profile

log = logging.getLogger("paperfeed")

DEBUG_CANDIDATES = 10


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paperfeed",
        description="Daily personalized arXiv feed, delivered as a Feishu card.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the card JSON to stdout instead of sending it",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="check Bailian and Zotero access, then exit",
    )
    return parser


def _configure_logging(debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )


def _build(settings: Settings) -> dict[str, Any] | None:
    """Run the feed pipeline. Returns the card message, or None when nothing is sent."""
    profile = load_profile(settings.zotero)

    candidates = arxiv_source.fetch(settings.arxiv)
    if settings.debug:
        candidates = candidates[:DEBUG_CANDIDATES]
    log.info("%d candidate(s) to rank", len(candidates))

    scored = rank.rank(
        profile,
        candidates,
        settings=settings.bailian,
        avoid_penalty=settings.avoid_penalty,
    )
    kept = rank.select_top(scored, min_score=settings.min_score, max_papers=settings.max_papers)
    log.info(
        "kept %d paper(s) at min_score=%.2f%s",
        len(kept),
        settings.min_score,
        f" (best {kept[0].final:.2f})" if kept else "",
    )
    if not kept:
        log.info("nothing cleared the threshold today; not sending")
        return None

    described = describe.describe_all(
        profile,
        kept,
        settings=settings.bailian,
        fallback_language=settings.language,
    )
    if not described:
        log.warning("no description succeeded; not sending an empty card")
        return None

    card = feishu.build_card(
        date=datetime.now(UTC).strftime("%Y-%m-%d"),
        collected=len(candidates),
        kept=described,
        categories=settings.arxiv.categories,
        color=settings.feishu.card_color,
    )
    return feishu.build_message(card)


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = Settings()
    except (SettingsError, ValidationError) as exc:
        logging.basicConfig(level=logging.INFO)
        log.error("invalid configuration: %s", exc)
        return 2

    _configure_logging(settings.debug)
    if args.check:
        return doctor.report(doctor.check(settings))

    try:
        settings.require_runtime()
        if args.dry_run:
            message = _build(settings)
            if message is not None:
                print(json.dumps(message, ensure_ascii=False, indent=2))
            return 0

        webhook = settings.require_webhook()
        message = _build(settings)
        if message is not None:
            feishu.send(webhook, message)
        return 0
    except ConfigError as exc:
        log.error("%s", exc)
        return 2
    except PaperfeedError as exc:
        log.error("%s", exc)
        return 1
    except Exception:  # noqa: BLE001 - a cron job should log, not dump a traceback
        log.exception("unexpected failure")
        return 1


def main() -> None:
    raise SystemExit(run(sys.argv[1:]))
