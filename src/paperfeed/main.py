"""Orchestration and CLI.

`paperfeed`              fetch → rank → describe → send
`paperfeed --dry-run`    the same, printing the card JSON instead of sending
`paperfeed --check`      prove the host, key, model ids and Zotero access work

The step between rank and describe is a full-text fetch for the papers that
survive `select_top`: the card holds at most `max_papers` (default 10) papers, so
ten HTML pages a day is cheap, and ranking never sees them.
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

from . import arxiv_source, describe, doctor, feishu, full_text, rank
from .bailian import Bailian
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


def _build(settings: Settings, bailian: Bailian) -> dict[str, Any] | None:
    """Run the feed pipeline. Returns the card message, or None when nothing is sent."""
    profile = load_profile(settings.zotero)

    candidates = arxiv_source.fetch(settings.arxiv)
    if settings.debug:
        candidates = candidates[:DEBUG_CANDIDATES]
    log.info("%d candidate(s) to rank", len(candidates))

    scored = rank.rank(
        profile,
        candidates,
        bailian=bailian,
        avoid_penalty=settings.avoid_penalty,
        concurrency=settings.bailian.decision_concurrency,
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

    sources = full_text.attach(kept, settings=settings.full_text)
    described = describe.describe_all(
        profile,
        sources,
        bailian=bailian,
        fallback_language=settings.language,
        concurrency=settings.bailian.chat_concurrency,
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
        usage=bailian.usage,
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
        # Validate the webhook before the slow stages: a delivery run must not
        # rank 300 papers only to discover it cannot send.
        webhook = None if args.dry_run else settings.require_webhook()

        with Bailian(settings.bailian) as bailian:
            try:
                message = _build(settings, bailian)
            finally:
                # The only record of spend on days when no card is sent: ranking
                # ran, but nothing cleared the threshold or no description survived.
                log.info(
                    "tokens: rank %d in · describe %d in / %d out",
                    bailian.usage.rank.input_tokens,
                    bailian.usage.describe.input_tokens,
                    bailian.usage.describe.output_tokens,
                )
        if message is None:
            return 0
        if webhook is None:
            print(json.dumps(message, ensure_ascii=False, indent=2))
        else:
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
