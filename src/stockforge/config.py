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
    # Bankr LLM Gateway (verified: https://llm.bankr.bot, OpenAI-compatible at
    # /v1/chat/completions, X-API-Key or Bearer). This is how fees pay for compute.
    # Separate LLM key is optional — falls back to BANKR_API_KEY. Beta-gated.
    bankr_llm_key: str = Field(default="", alias="BANKR_LLM_KEY")
    bankr_llm_base: str = Field(default="https://llm.bankr.bot/v1", alias="BANKR_LLM_BASE")

    # ---- Launch policy -------------------------------------------------------
    default_chain: Chain = Field(default="base", alias="STOCKFORGE_DEFAULT_CHAIN")
    daily_launch_budget: int = Field(default=3, alias="STOCKFORGE_DAILY_LAUNCH_BUDGET")
    min_attention_score: int = Field(default=65, alias="STOCKFORGE_MIN_ATTENTION_SCORE")
    tick_seconds: int = Field(default=60, alias="STOCKFORGE_TICK_SECONDS")
    disable_vesting: bool = Field(default=False, alias="STOCKFORGE_DISABLE_VESTING")

    # ---- Fee capture / treasury ----------------------------------------------
    # Single address all creator fees route to (fee recipient at launch + claim
    # destination). Defaults to BANKR_BENEFICIARY_ADDRESS when unset.
    treasury_address: str = Field(default="", alias="STOCKFORGE_TREASURY_ADDRESS")
    # Automatically claim accrued fees during fee sweeps (still subject to
    # dry-run + approval). False = monitor/report only, never claim.
    auto_claim: bool = Field(default=True, alias="STOCKFORGE_AUTO_CLAIM")
    # Minimum claimable WETH before a claim is triggered (avoids dust claims/gas).
    fee_claim_min_weth: float = Field(default=0.001, alias="STOCKFORGE_FEE_CLAIM_MIN_WETH")
    # Ticks between fee sweeps.
    fee_sweep_every_ticks: int = Field(default=6, alias="STOCKFORGE_FEE_SWEEP_EVERY_TICKS")

    # ---- Multi-wallet (one honest operation, several wallets) ----------------
    # Optional JSON list of wallets for key segregation / opsec / treasury
    # splitting / SPOF reduction. Each: {"id","fee_recipient","api_key",
    # "private_key","club"}. Empty = single 'main' wallet from the treasury.
    # NOT for disguising one operator as many creators — attribution is tracked.
    wallets_json: str = Field(default="", alias="STOCKFORGE_WALLETS")
    # Per-wallet daily launch cap (each wallet independently respects Bankr's
    # 50/100 + 1/min). STOCKFORGE_DAILY_LAUNCH_BUDGET remains the GLOBAL hard
    # ceiling across all wallets.
    per_wallet_daily_cap: int = Field(default=50, alias="STOCKFORGE_PER_WALLET_DAILY_CAP")

    # ---- Promotion (operator-gated) ------------------------------------------
    # On launch, compose a promo kit (tweet + one-liner + link) and notify the
    # operator. Never auto-posts to public social — that stays human-gated.
    promo_enabled: bool = Field(default=True, alias="STOCKFORGE_PROMO_ENABLED")
    # Optional base for building a launch link from a token address when the
    # deploy response has no pool URL (e.g. a DEX explorer base).
    promo_link_base: str = Field(default="", alias="STOCKFORGE_PROMO_LINK_BASE")

    # ---- Telegram ------------------------------------------------------------
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")

    # ---- Concept forge -------------------------------------------------------
    # "none" = deterministic template (no external calls).
    # "openai_compatible" = any OpenAI-style endpoint (FORGE_LLM_* below).
    # "bankr" = the Bankr LLM Gateway (llm.bankr.bot) — closes the fees->compute
    #           loop: the agent's own trading fees pay for its concept-generation
    #           compute. Uses BANKR_LLM_KEY (or BANKR_API_KEY).
    forge_llm_provider: Literal["none", "openai_compatible", "bankr"] = Field(
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
    # Real attention source: financial-news volume via Google News RSS (no key).
    # Off by default so the pipeline makes no external calls unless opted in.
    news_source_enabled: bool = Field(default=False, alias="STOCKFORGE_NEWS_SOURCE")
    news_freshness_hours: int = Field(default=24, alias="STOCKFORGE_NEWS_FRESHNESS_HOURS")

    # Elon-tweet source: propose a launch when a tweet resolves a ticker AND hits
    # an engagement/hype bar. Ingestion via X API bearer token, Grok live-search
    # (xAI key), or the manual /elon inbox. Off by default. Still fully gated.
    elon_source_enabled: bool = Field(default=False, alias="STOCKFORGE_ELON_SOURCE")
    elon_user_id: str = Field(default="44196397", alias="STOCKFORGE_ELON_USER_ID")
    elon_min_engagement: int = Field(default=10000, alias="STOCKFORGE_ELON_MIN_ENGAGEMENT")
    elon_provider: Literal["inbox", "x_api", "grok"] = Field(
        default="inbox", alias="STOCKFORGE_ELON_PROVIDER"
    )
    x_bearer_token: str = Field(default="", alias="X_BEARER_TOKEN")
    xai_api_key: str = Field(default="", alias="XAI_API_KEY")
    xai_base_url: str = Field(default="https://api.x.ai/v1", alias="XAI_BASE_URL")
    xai_model: str = Field(default="grok-4", alias="XAI_MODEL")

    # ---- Metadata enrichment -------------------------------------------------
    # Generate a token image (xAI Imagine) and inject it into the launch — but
    # ONLY for strong/meme-worthy launches (score >= image_min_score), not every
    # token. Needs XAI_API_KEY. Off by default.
    image_gen_enabled: bool = Field(default=False, alias="STOCKFORGE_IMAGE_GEN")
    image_min_score: int = Field(default=80, alias="STOCKFORGE_IMAGE_MIN_SCORE")
    xai_image_model: str = Field(default="grok-2-image", alias="XAI_IMAGE_MODEL")
    # Optional website injected into every launch (blank = none).
    default_website: str = Field(default="", alias="STOCKFORGE_DEFAULT_WEBSITE")

    @field_validator("default_chain", "bankr_backend", "forge_llm_provider", "elon_provider", mode="before")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower() if isinstance(v, str) else v

    @property
    def watchlist(self) -> list[str]:
        return [t.strip().upper() for t in self.watchlist_raw.split(",") if t.strip()]

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def treasury(self) -> str:
        """The address all creator fees route to (fee recipient + claim target).
        Explicit treasury wins; otherwise the Bankr beneficiary is the treasury."""
        return self.treasury_address or self.bankr_beneficiary_address

    @property
    def autonomous(self) -> bool:
        """True when real launches proceed with NO Telegram tap (approval off and
        not dry-run). The kill switch, budget, and rate limits still apply."""
        return not self.require_approval and not self.dry_run

    @property
    def llm_gateway_key(self) -> str:
        """Key for the Bankr LLM Gateway — dedicated LLM key, else the API key."""
        return self.bankr_llm_key or self.bankr_api_key

    @property
    def llm_gateway_configured(self) -> bool:
        return self.forge_llm_provider == "bankr" and bool(self.llm_gateway_key)

    def forge_effective(self) -> tuple[str, str, str, str]:
        """Resolve (base_url, api_key, model, auth_style) for the active LLM
        provider. auth_style is 'x-api-key' for the Bankr Gateway (bk_ keys) and
        'bearer' for generic OpenAI-compatible endpoints."""
        if self.forge_llm_provider == "bankr":
            return (self.bankr_llm_base, self.llm_gateway_key, self.forge_llm_model, "x-api-key")
        return (self.forge_llm_base_url, self.forge_llm_api_key, self.forge_llm_model, "bearer")

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
            "treasury": self.treasury or "unset",
            "autonomous": self.autonomous,
            "auto_claim": self.auto_claim,
            "fee_claim_min_weth": self.fee_claim_min_weth,
            "default_chain": self.default_chain,
            "daily_launch_budget": self.daily_launch_budget,
            "min_attention_score": self.min_attention_score,
            "telegram_enabled": self.telegram_enabled,
            "forge_llm_provider": self.forge_llm_provider,
            "llm_gateway": "configured" if self.llm_gateway_configured else "off",
            "bankr_llm_key": mask(self.bankr_llm_key),
            "news_source": self.news_source_enabled,
            "watchlist": self.watchlist,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
