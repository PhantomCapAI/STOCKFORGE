"""Domain models shared across the pipeline: Signal -> Concept -> LaunchRequest -> LaunchResult -> Fees."""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

Chain = Literal["base", "robinhood"]


def _id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> float:
    return time.time()


class Signal(BaseModel):
    """A detected stock narrative worth (maybe) forging a token around."""

    id: str = Field(default_factory=_id)
    ticker: str
    headline: str = ""
    attention_score: float = 0.0  # 0-100
    sources: list[str] = Field(default_factory=list)
    detected_at: float = Field(default_factory=_now)
    meta: dict[str, Any] = Field(default_factory=dict)


class Concept(BaseModel):
    """A generated token concept ready to be launched."""

    id: str = Field(default_factory=_id)
    signal_id: str | None = None
    paired_ticker: str  # the stock this narrative rides (NVDA, GME, ...)
    name: str
    symbol: str
    thesis: str
    image_prompt: str = ""
    image_url: str = ""
    launch_tweet: str = ""
    source_tweet_url: str = ""  # e.g. the Elon tweet that triggered this concept
    uniqueness_score: float = 0.0  # 0-1, higher = less sloppy/duplicative
    created_at: float = Field(default_factory=_now)

    def slug(self) -> str:
        return f"{self.symbol}:{self.name}".lower()


class LaunchStatus(str, Enum):
    PENDING = "pending"  # created, not yet submitted
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    REJECTED = "rejected"  # human said no
    SIMULATED = "simulated"  # dry-run / --simulate


class PairStatus(str, Enum):
    """Outcome of a stock-pairing intent (pool quoted in NVDA/GME/... vs WETH).

    Bankr does not publicly document a first-class pairing parameter, so this is
    inferred from the launch response, best-effort and honest:
    """

    NOT_REQUESTED = "not_requested"  # standard (WETH) launch — no stock pair asked for
    REQUESTED = "requested"  # asked for, outcome not yet verifiable (dry-run / pending job)
    ACCEPTED = "accepted"  # evidence the pool is quoted in the requested stock
    DEGRADED = "degraded"  # launched, but paired standard (WETH) instead of the stock
    REJECTED = "rejected"  # launch failed / pairing not honored and no token produced


class LaunchRequest(BaseModel):
    """Everything Bankr needs to deploy. `pair_with` is the stock-pairing intent."""

    id: str = Field(default_factory=_id)
    concept_id: str
    name: str
    symbol: str
    chain: Chain = "base"
    wallet_id: str = "main"  # which operation wallet launched this (attribution)
    image_url: str = ""
    tweet_url: str = ""
    website: str = ""
    fee_recipient: str = ""  # X/Farcaster handle, ENS, or 0x address; blank = default wallet
    fee_recipient_type: Literal["x", "farcaster", "ens", "address", ""] = ""
    disable_vesting: bool = False
    # UNVERIFIED intent: pair the new token against a stock (Robinhood Chain).
    # Bankr's public docs pair pools against WETH; stock-pairing is passed through
    # as a natural-language hint and an explicit field for when/if Bankr exposes it.
    pair_with: str = ""  # e.g. "NVDA" — empty means default (WETH) pairing
    created_at: float = Field(default_factory=_now)


class LaunchResult(BaseModel):
    id: str = Field(default_factory=_id)
    request_id: str
    status: LaunchStatus
    wallet_id: str = "main"  # attribution: which operation wallet launched it
    token_address: str = ""
    pool_id: str = ""
    initializer: str = ""  # Fees Manager contract for later claims
    tx_hash: str = ""
    pool_url: str = ""
    job_id: str = ""  # Bankr async job id (REST backend)
    pair_requested: str = ""  # the stock ticker we asked to pair against ("" = none)
    pair_status: PairStatus = PairStatus.NOT_REQUESTED
    quote_labels: list[str] = Field(default_factory=list)  # pool quote assets seen in the response
    raw: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    finished_at: float = Field(default_factory=_now)


class FeeSnapshot(BaseModel):
    token_address: str
    beneficiary: str
    claimable_weth: float = 0.0
    claimable_token: float = 0.0
    claimed_weth: float = 0.0
    lifetime_weth: float = 0.0
    pool_id: str = ""
    initializer: str = ""
    at: float = Field(default_factory=_now)


class ApprovalKind(str, Enum):
    LAUNCH = "launch"
    CLAIM = "claim"


class Approval(BaseModel):
    id: str = Field(default_factory=_id)
    kind: ApprovalKind
    ref_id: str  # LaunchRequest.id or a claim batch id
    summary: str
    status: Literal["pending", "approved", "rejected", "expired"] = "pending"
    created_at: float = Field(default_factory=_now)
    decided_at: float | None = None
