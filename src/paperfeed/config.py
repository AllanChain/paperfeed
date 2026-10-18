"""Configuration: environment (and `.env`) into validated settings.

Every field has a default so the package imports and tests run with no
credentials at all. `Settings.require_runtime()` and `Settings.require_webhook()`
are the only places that demand the real ones, which keeps "missing secret" a
startup error with a readable message instead of a 401 halfway through a run.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import PaperfeedError

DEFAULT_BAILIAN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_CHAT_MODEL = "qwen3.8-flash"


class ZoteroSettings(BaseModel):
    user_id: str = ""
    api_key: SecretStr | None = None
    library_type: Literal["user", "group"] = "user"
    collection: str = "paperfeed/interests"
    note_title: str = "paperfeed profile"


class ArxivSettings(BaseModel):
    categories: list[str] = Field(default_factory=lambda: ["cond-mat.str-el", "quant-ph"])
    announce_types: list[str] = Field(default_factory=lambda: ["new", "cross"])
    max_candidates: int = 300


class FullTextSettings(BaseModel):
    # The body of every paper that reaches the card, for the description only.
    enabled: bool = True
    # A guard for pathological papers, not a working limit: measured arXiv bodies
    # run 12k-55k tokens, and ~3.5 chars/token puts 100k tokens near this cap.
    max_chars: int = 350_000
    concurrency: int = 2
    timeout: float = 60.0


class BailianSettings(BaseModel):
    base_url: str = DEFAULT_BAILIAN_BASE_URL
    api_key: SecretStr | None = None
    decision_model: str = "decision-model-preview"
    chat_model: str = DEFAULT_CHAT_MODEL
    decision_concurrency: int = 8
    chat_concurrency: int = 4
    # Thinking off: qwen3-family ids otherwise emit reasoning tokens nobody reads
    # and ignore temperature. JSON (not a bool) because the parameter name varies
    # per family; `{}` is the fallback for an id that rejects it.
    chat_extra_body: dict[str, Any] = Field(default_factory=lambda: {"enable_thinking": False})


class FeishuSettings(BaseModel):
    webhook_url: SecretStr | None = None
    card_color: str = "blue"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PAPERFEED_",
        env_nested_delimiter="__",
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    zotero: ZoteroSettings = Field(default_factory=ZoteroSettings)
    arxiv: ArxivSettings = Field(default_factory=ArxivSettings)
    full_text: FullTextSettings = Field(default_factory=FullTextSettings)
    bailian: BailianSettings = Field(default_factory=BailianSettings)
    feishu: FeishuSettings = Field(default_factory=FeishuSettings)

    max_papers: int = 10
    min_score: float = 0.60
    avoid_penalty: float = 0.5
    language: str = "English"
    debug: bool = False

    def require_runtime(self) -> None:
        """Raise unless every credential a normal run needs is present."""
        required = [
            ("PAPERFEED_ZOTERO__USER_ID", self.zotero.user_id),
            ("PAPERFEED_ZOTERO__API_KEY", _plain(self.zotero.api_key)),
            ("PAPERFEED_BAILIAN__API_KEY", _plain(self.bailian.api_key)),
        ]
        missing = [name for name, value in required if not value]
        if missing:
            raise ConfigError("missing required settings: " + ", ".join(missing))

    def require_webhook(self) -> str:
        """Return the webhook URL, raising unless a delivery run can send."""
        url = _plain(self.feishu.webhook_url)
        if not url:
            raise ConfigError("PAPERFEED_FEISHU__WEBHOOK_URL is not set")
        return url


class ConfigError(PaperfeedError):
    """Configuration is incomplete or contradictory."""


def _plain(value: SecretStr | None) -> str:
    return value.get_secret_value() if value is not None else ""
