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


class GrokTweetProvider:
    """Fetches recent tweets via xAI Grok, which has native real-time access to
    X. Uses the standard OpenAI-compatible chat-completions endpoint (no
    undocumented handle-search params) and asks Grok to return recent original
    posts as strict JSON. LLM-mediated and best-effort: engagement counts may be
    approximate or absent, so the mark for Grok-sourced tweets leans on the
    ticker + hype/cashtag signal. Returns [] on any error."""

    def __init__(
        self,
        api_key: str,
        user_id: str = ELON_USER_ID,
        *,
        handle: str = "elonmusk",
        base_url: str = "https://api.x.ai/v1",
        model: str = "grok-4",
        max_tweets: int = 5,
        timeout: float = 30.0,
    ):
        self.api_key = api_key
        self.user_id = user_id
        self.handle = handle
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_tweets = max_tweets
        self.timeout = timeout

    async def fetch_recent(self) -> list[Tweet]:
        if not self.api_key:
            return []
        system = (
            "You have real-time access to X (Twitter). Return ONLY a JSON object, "
            "no prose."
        )
        user = (
            f"Give @{self.handle}'s {self.max_tweets} most recent ORIGINAL posts "
            "(not replies or reposts) from the last 24 hours. Respond as JSON: "
            '{"tweets":[{"id":"<tweet id or url>","text":"<full text>",'
            '"like_count":<int>,"retweet_count":<int>}]}. Use 0 if a count is unknown.'
        )
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions", json=body, headers=headers
                )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return parse_grok_tweets(content, handle=self.handle)
        except Exception as e:  # noqa: BLE001
            log.warning("Grok tweet fetch failed: %r", e)
            return []


def parse_grok_tweets(content: str, *, handle: str = "elonmusk") -> list[Tweet]:
    """Extract Tweets from Grok's JSON reply. Tolerant of code fences / stray
    prose around the JSON object."""
    import json
    import re as _re

    m = _re.search(r"\{.*\}", content, _re.DOTALL)
    if not m:
        return []
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out: list[Tweet] = []
    for row in obj.get("tweets", []) or []:
        text = (row.get("text") or "").strip()
        if not text:
            continue
        out.append(
            Tweet(
                id=str(row.get("id", "") or ""),
                text=text,
                created_at=datetime.now(UTC),
                like_count=int(row.get("like_count", 0) or 0),
                retweet_count=int(row.get("retweet_count", 0) or 0),
            )
        )
    return out


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
    handle: str = "elonmusk"
    name: str = "elon-tweets"

    def _tweet_url(self, tweet_id: str) -> str:
        if not tweet_id:
            return ""
        if tweet_id.startswith("http"):
            return tweet_id
        return f"https://x.com/{self.handle}/status/{tweet_id}"

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
                        "tweet_url": self._tweet_url(tw.id),
                        "engagement": tw.engagement,
                        "provider": "elon-tweets",
                        "reason": mark.reason,
                    },
                )
            )
        return signals
