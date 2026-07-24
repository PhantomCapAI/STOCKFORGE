"""Central configuration. All secrets come from the environment / .env — never hardcoded.

Loaded once via `get_settings()`. Anything that touches money is off by default:
`dry_run=True` and `require_approval=True`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Chain = Literal["base", "robinhood"]
BankrBackend = Literal["cli", "rest"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Global safety -------------------------------------------------------
    dry_run: bool = Field(default=True, alias="STOCKFORGE_DRY_RUN")
    require_approval: bool = Field(default=True, alias="STOCKFORGE_REQUIRE_APPROVAL")
    log_level: str = Field(default="INFO", alias="STOCKFORGE_LOG_LEVEL")
    db_path: str = Field(default="./data/stockforge.sqlite", alias="STOCKFORGE_DB_PATH")

    # ---- Bankr ---------------------------------------------------------------
    bankr_backend: BankrBackend = Field(default="rest", alias="BANKR_BACKEND")
    bankr_api_base: str = Field(default="https://api.bankr.bot", alias="BANKR_API_BASE")
    bankr_api_key: str = Field(default="", alias="BANKR_API_KEY")
    bankr_cli_bin: str = Field(default="bankr", alias="BANKR_CLI_BIN")
    bankr_private_key: str = Field(default="", alias="BANKR_PRIVATE_KEY")
    bankr_beneficiary_address: str = Field(default="", alias="BANKR_BENEFICIARY_ADDRESS")

    # ---- Launch policy -------------------------------------------------------
    default_chain: Chain = Field(default="base", alias="STOCKFORGE_DEFAULT_CHAIN")
    daily_launch_budget: int = Field(default=3, alias="STOCKFORGE_DAILY_LAUNCH_BUDGET")
    min_attention_score: int = Field(default=65, alias="STOCKFORGE_MIN_ATTENTION_SCORE")
    tick_seconds: int = Field(default=60, alias="STOCKFORGE_TICK_SECONDS")
    disable_vesting: bool = Field(default=False, alias="STOCKFORGE_DISABLE_VESTING")

    # ---- Telegram ------------------------------------------------------------
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")

    # ---- Concept forge -------------------------------------------------------
    forge_llm_provider: Literal["none", "openai_compatible"] = Field(
        default="none", alias="FORGE_LLM_PROVIDER"
    )
    forge_llm_base_url: str = Field(
        default="https://openrouter.ai/api/v1", alias="FORGE_LLM_BASE_URL"
    )
    forge_llm_api_key: str = Field(default="", alias="FORGE_LLM_API_KEY")
    forge_llm_model: str = Field(default="anthropic/claude-sonnet-4-6", alias="FORGE_LLM_MODEL")

    # ---- Signal --------------------------------------------------------------
    watchlist_raw: str = Field(
        default="NVDA,GME,TSLA,HOOD,SPY,AMD,PLTR,MSTR", alias="STOCKFORGE_WATCHLIST"
    )

    @field_validator("default_chain", "bankr_backend", "forge_llm_provider", mode="before")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower() if isinstance(v, str) else v

    @property
    def watchlist(self) -> list[str]:
        return [t.strip().upper() for t in self.watchlist_raw.split(",") if t.strip()]

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    def redacted(self) -> dict:
        """Config snapshot safe to log — secrets masked."""

        def mask(v: str) -> str:
            return "set" if v else "unset"

        return {
            "dry_run": self.dry_run,
            "require_approval": self.require_approval,
            "bankr_backend": self.bankr_backend,
            "bankr_api_base": self.bankr_api_base,
            "bankr_api_key": mask(self.bankr_api_key),
            "bankr_private_key": mask(self.bankr_private_key),
            "beneficiary": self.bankr_beneficiary_address or "unset",
            "default_chain": self.default_chain,
            "daily_launch_budget": self.daily_launch_budget,
            "min_attention_score": self.min_attention_score,
            "telegram_enabled": self.telegram_enabled,
            "forge_llm_provider": self.forge_llm_provider,
            "watchlist": self.watchlist,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
