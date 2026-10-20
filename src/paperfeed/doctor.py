"""`--check` diagnostics: one row per external dependency."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .bailian import Bailian
from .config import Settings
from .zotero import load_profile


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def _attempt(name: str, probe: Callable[[], str]) -> CheckResult:
    """Run one probe; any failure becomes a reportable row, never an abort."""
    try:
        return CheckResult(name, True, probe())
    except Exception as exc:  # noqa: BLE001 - report, never abort the check
        return CheckResult(name, False, str(exc))


def _decision_check(settings: Settings) -> CheckResult:
    def probe() -> str:
        with Bailian(settings.bailian) as client:
            response = client.decide(
                {
                    "state": {"text": "paperfeed connectivity check"},
                    "questions": {
                        "reachable": {
                            "type": "noul",
                            "instructions": "Is this a connectivity check?",
                        }
                    },
                }
            )
        answer = response.get("answers", {}).get("reachable", {})
        return f"noul={answer.get('noul')!r}"

    return _attempt(f"decision ({settings.bailian.decision_model})", probe)


def _chat_check(settings: Settings) -> CheckResult:
    def probe() -> str:
        with Bailian(settings.bailian) as client:
            text = client.chat(
                system="Reply with the single word: ok",
                user="ping",
                max_tokens=8,
            )
        return f"reply={text!r}"

    return _attempt(f"chat ({settings.bailian.chat_model})", probe)


def _profile_check(settings: Settings) -> CheckResult:
    def probe() -> str:
        profile = load_profile(settings.zotero)
        return (
            f"focus={profile.focus[:60]!r} topics={len(profile.topics)} "
            f"anchors={len(profile.saved_titles)}"
        )

    return _attempt("zotero profile", probe)


def check(settings: Settings) -> list[CheckResult]:
    return [_decision_check(settings), _chat_check(settings), _profile_check(settings)]


def report(results: Sequence[CheckResult]) -> int:
    width = max((len(result.name) for result in results), default=0)
    for result in results:
        status = "OK  " if result.ok else "FAIL"
        print(f"{status}  {result.name:<{width}}  {result.detail}")
    return 0 if all(result.ok for result in results) else 1
