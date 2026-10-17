from __future__ import annotations

import pytest

from conftest import make_settings
from paperfeed.config import ConfigError, Settings


def test_defaults_boot_without_any_env() -> None:
    settings = Settings(_env_file=None)
    assert settings.arxiv.categories == ["cond-mat.str-el", "quant-ph"]
    assert settings.arxiv.announce_types == ["new", "cross"]
    assert settings.bailian.decision_model == "decision-model-preview"
    assert settings.bailian.chat_model == "qwen3.8-flash"
    assert settings.bailian.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert settings.bailian.chat_extra_body == {"enable_thinking": False}
    assert settings.max_papers == 10
    assert settings.min_score == pytest.approx(0.60)
    assert settings.avoid_penalty == pytest.approx(0.5)
    assert settings.feishu.card_color == "blue"
    assert settings.debug is False


def test_nested_env_and_json_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERFEED_ZOTERO__USER_ID", "7654321")
    monkeypatch.setenv("PAPERFEED_ZOTERO__COLLECTION", "physics/papers")
    monkeypatch.setenv("PAPERFEED_ARXIV__CATEGORIES", '["cond-mat.mtrl-sci","physics.comp-ph"]')
    monkeypatch.setenv("PAPERFEED_ARXIV__ANNOUNCE_TYPES", '["new"]')
    monkeypatch.setenv("PAPERFEED_ARXIV__MAX_CANDIDATES", "25")
    monkeypatch.setenv("PAPERFEED_BAILIAN__CHAT_EXTRA_BODY", '{"enable_thinking": false}')
    monkeypatch.setenv("PAPERFEED_MIN_SCORE", "0.5")
    monkeypatch.setenv("PAPERFEED_DEBUG", "true")

    settings = Settings(_env_file=None)

    assert settings.zotero.user_id == "7654321"
    assert settings.zotero.collection == "physics/papers"
    assert settings.arxiv.categories == ["cond-mat.mtrl-sci", "physics.comp-ph"]
    assert settings.arxiv.announce_types == ["new"]
    assert settings.arxiv.max_candidates == 25
    assert settings.bailian.chat_extra_body == {"enable_thinking": False}
    assert settings.min_score == pytest.approx(0.5)
    assert settings.debug is True


def test_secrets_are_not_exposed_by_repr() -> None:
    settings = make_settings()
    assert "bailian-key" not in repr(settings)
    assert "zotero-key" not in repr(settings)
    assert settings.bailian.api_key is not None
    assert settings.bailian.api_key.get_secret_value() == "bailian-key"


def test_require_runtime_ok() -> None:
    make_settings().require_runtime()


def test_require_runtime_does_not_need_a_webhook() -> None:
    make_settings(feishu={"webhook_url": None}).require_runtime()


def test_require_runtime_names_every_missing_setting() -> None:
    settings = Settings(_env_file=None)
    with pytest.raises(ConfigError) as excinfo:
        settings.require_runtime()
    message = str(excinfo.value)
    for name in (
        "PAPERFEED_ZOTERO__USER_ID",
        "PAPERFEED_ZOTERO__API_KEY",
        "PAPERFEED_BAILIAN__API_KEY",
    ):
        assert name in message


def test_require_webhook_returns_the_url() -> None:
    assert make_settings().require_webhook() == "https://open.feishu.cn/open-apis/bot/v2/hook/test"


def test_require_webhook_names_the_setting() -> None:
    settings = make_settings(feishu={"webhook_url": None})
    with pytest.raises(ConfigError, match="PAPERFEED_FEISHU__WEBHOOK_URL"):
        settings.require_webhook()


def test_empty_env_var_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERFEED_BAILIAN__API_KEY", "")
    settings = Settings(_env_file=None)
    assert settings.bailian.api_key is None
