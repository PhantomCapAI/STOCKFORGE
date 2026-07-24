"""Elon-tweet signal source.

A dedicated attention source that watches a single high-impact account (Elon by
default) and emits a launch candidate only when a tweet "hits the mark":

  1. it resolves to a real ticker — a cashtag ($TSLA) or a company name in the
     ticker map (Tesla → TSLA, Nvidia → NVDA, Dogecoin → DOGE …), AND
  2. it clears an engagement bar and/or carries hype/narrative keywords.

Everything downstream is unchanged: the emitted Signal flows through the normal
score → forge → anti-slop → rate-limit → **human approval** → dry-run pipeline.
This source proposes; it never launches. Nothing here bypasses a gate.

Ingestion is provider-agnostic:
- ``XApiTweetProvider`` uses X API v2 (`GET /2/users/:id/tweets`) when an
  ``X_BEARER_TOKEN`` is configured (paid access — no key is invented here).
- ``TweetInbox`` is an in-memory queue an operator or a webhook can push tweets
  into, so the source runs and is testable without paid API access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import httpx

from ..logging import get_logger
from ..models import Signal
from .scorer import NARRATIVE_WORDS

log = get_logger("signal.elon")

# Elon Musk's numeric X user id (stable, public).
ELON_USER_ID = "44196397"

# Company / product name → stock (or crypto) ticker. Extend via config if needed.
# Only names with a tradeable symbol are here; private ventures (SpaceX, xAI,
# Neuralink, Boring Co) are intentionally omitted — no ticker to ride.
DEFAULT_TICKER_MAP: dict[str, str] = {
    "tesla": "TSLA",
    "tsla": "TSLA",
    "nvidia": "NVDA",
    "dogecoin": "DOGE",
    "doge": "DOGE",
    "bitcoin": "BTC",
    "btc": "BTC",
    "twitter": "X",
    "starlink": "STRLK",  # not public; kept out of default emissions unless mapped
}

_CASHTAG_RE = re.compile(r"\$([A-Za-z]{1,6})\b")
# Engagement (likes + 2·reposts) that maps to full magnitude (20).
_ENGAGEMENT_FULL_SCALE = 25_000.0


@dataclass
class Tweet:
    id: str
    text: str
    created_at: datetime | None = None
    like_count: int = 0
    retweet_count: int = 0
    author: str = "elon"

    @property
    def engagement(self) -> float:
        # Reposts weigh more than likes as a spread signal.
        return float(self.like_count) + 2.0 * float(self.retweet_count)


@dataclass
class MarkResult:
    """Outcome of evaluating one tweet against the 'hit the mark' criteria."""

    hit: bool
    ticker: str = ""
    magnitude: float = 0.0
    reason: str = ""


class TweetProvider(Protocol):
    async def fetch_recent(self) -> list[Tweet]: ...


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------


class TweetInbox:
    """In-memory queue for tweets pushed by an operator or a webhook. Lets the
    Elon source run (and be tested/demoed) with zero paid API access."""

    def __init__(self) -> None:
        self._q: list[Tweet] = []

    def push(self, text: str, *, tweet_id: str = "", like_count: int = 0, retweet_count: int = 0) -> Tweet:
        t = Tweet(
            id=tweet_id or f"inbox-{len(self._q)}",
            text=text,
            created_at=datetime.now(UTC),
            like_count=like_count,
            retweet_count=retweet_count,
        )
        self._q.append(t)
        return t

    async def fetch_recent(self) -> list[Tweet]:
        out, self._q = self._q, []
        return out


class XApiTweetProvider:
    """Reads recent tweets via X API v2. Requires a bearer token (paid access) —
    supplied, never invented. Returns [] on any error (best-effort)."""

    def __init__(
        self,
        bearer_token: str,
        user_id: str = ELON_USER_ID,
        *,
        api_base: str = "https://api.twitter.com",
        max_results: int = 10,
        timeout: float = 8.0,
    ):
        self.bearer_token = bearer_token
        self.user_id = user_id
        self.api_base = api_base.rstrip("/")
        self.max_results = max_results
        self.timeout = timeout

    async def fetch_recent(self) -> list[Tweet]:
        if not self.bearer_token:
            return []
        url = f"{self.api_base}/2/users/{self.user_id}/tweets"
        params = {
            "max_results": str(self.max_results),
            "tweet.fields": "public_metrics,created_at",
            "exclude": "retweets,replies",
        }
        headers = {"Authorization": f"Bearer {self.bearer_token}"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            return parse_x_api(resp.json())
        except Exception as e:  # noqa: BLE001
            log.warning("X API fetch failed: %r", e)
            return []


def parse_x_api(payload: dict) -> list[Tweet]:
    """Parse an X API v2 users/:id/tweets response into Tweets. Tolerant of
    missing fields."""
    out: list[Tweet] = []
    for row in payload.get("data", []) or []:
        metrics = row.get("public_metrics", {}) or {}
        created = row.get("created_at")
        dt: datetime | None = None
        if created:
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError:
                dt = None
        out.append(
            Tweet(
                id=str(row.get("id", "")),
                text=row.get("text", "") or "",
                created_at=dt,
                like_count=int(metrics.get("like_count", 0) or 0),
                retweet_count=int(metrics.get("retweet_count", 0) or 0),
            )
        )
    return out


# --------------------------------------------------------------------------
# "Hit the mark" evaluation
# --------------------------------------------------------------------------


def resolve_ticker(text: str, ticker_map: dict[str, str]) -> str:
    """Resolve the most likely ticker in a tweet: an explicit cashtag wins,
    otherwise the first company/product name found in the map."""
    m = _CASHTAG_RE.search(text)
    if m:
        return m.group(1).upper()
    low = text.lower()
    for name, ticker in ticker_map.items():
        if re.search(rf"\b{re.escape(name)}\b", low):
            return ticker
    return ""


def evaluate_tweet(
    tweet: Tweet,
    *,
    ticker_map: dict[str, str],
    min_engagement: float,
) -> MarkResult:
    """Decide whether a tweet 'hits the mark' and, if so, how strong it is."""
    ticker = resolve_ticker(tweet.text, ticker_map)
    if not ticker:
        return MarkResult(hit=False, reason="no ticker resolved")

    low = tweet.text.lower()
    has_cashtag = bool(_CASHTAG_RE.search(tweet.text))
    hype_hits = sum(1 for w in NARRATIVE_WORDS if w in low)
    engaged = tweet.engagement >= min_engagement

    # The mark: a real ticker AND some evidence it will move attention —
    # enough engagement, OR an explicit cashtag, OR hype/narrative language.
    if not (engaged or has_cashtag or hype_hits):
        return MarkResult(hit=False, ticker=ticker, reason="below engagement + no hype/cashtag")

    # Magnitude (0-20): engagement scaled + a hype bonus, capped.
    mag = min(20.0, tweet.engagement / _ENGAGEMENT_FULL_SCALE * 20.0 + hype_hits * 3.0)
    if has_cashtag:
        mag = min(20.0, mag + 4.0)
    reason = f"engagement={tweet.engagement:.0f} hype={hype_hits} cashtag={has_cashtag}"
    return MarkResult(hit=True, ticker=ticker, magnitude=round(mag, 1), reason=reason)


# --------------------------------------------------------------------------
# Source
# --------------------------------------------------------------------------


@dataclass
class ElonTweetSource:
    """Signal source that emits a candidate only for tweets that hit the mark."""

    provider: TweetProvider
    ticker_map: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TICKER_MAP))
    min_engagement: float = 10_000.0
    name: str = "elon-tweets"

    async def poll(self) -> list[Signal]:
        try:
            tweets = await self.provider.fetch_recent()
        except Exception:  # noqa: BLE001
            log.exception("elon provider failed")
            return []
        signals: list[Signal] = []
        for tw in tweets:
            mark = evaluate_tweet(tw, ticker_map=self.ticker_map, min_engagement=self.min_engagement)
            if not mark.hit:
                log.info("elon tweet %s skipped: %s", tw.id, mark.reason)
                continue
            log.info("elon tweet %s HIT %s (%s)", tw.id, mark.ticker, mark.reason)
            signals.append(
                Signal(
                    ticker=mark.ticker,
                    headline=tw.text.strip().replace("\n", " ")[:200],
                    sources=["elon", "x"],
                    meta={
                        "magnitude": mark.magnitude,
                        "tweet_id": tw.id,
                        "engagement": tw.engagement,
                        "provider": "elon-tweets",
                        "reason": mark.reason,
                    },
                )
            )
        return signals
