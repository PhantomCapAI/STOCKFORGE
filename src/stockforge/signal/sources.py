"""Signal sources.

A SignalSource yields candidate Signals. Phase 3 ships two safe, no-external-key
sources; real sources (X/Twitter trends, news APIs, on-chain flow) implement the
same `poll()` contract and drop in without orchestrator changes.
"""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Protocol
from urllib.parse import quote_plus

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..logging import get_logger
from ..models import Signal
from .scorer import NARRATIVE_WORDS

log = get_logger("signal.sources")


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.4, max=3),
    retry=retry_if_exception_type(httpx.TransportError),
    reraise=True,
)
async def _fetch(client: httpx.AsyncClient, url: str) -> str:
    """GET with a short exponential backoff on transient transport errors
    (connect/read timeouts). HTTP status errors are NOT retried — they surface
    to the caller and the ticker is skipped for this tick."""
    resp = await client.get(url)
    resp.raise_for_status()
    return resp.text


class SignalSource(Protocol):
    name: str

    async def poll(self) -> list[Signal]: ...


class WatchlistHeuristicSource:
    """Deterministic placeholder: emits low-score baseline signals for the
    watchlist so the pipeline is exercised end-to-end without any external API.
    Real attention only crosses the launch threshold once a real source (or a
    manual /launch) supplies genuine narrative evidence.
    """

    name = "watchlist-heuristic"

    def __init__(self, tickers: list[str]):
        self.tickers = tickers

    async def poll(self) -> list[Signal]:
        return [
            Signal(
                ticker=t,
                headline=f"{t} baseline watch",
                sources=["watchlist"],
                meta={"magnitude": 0.0},
            )
            for t in self.tickers
        ]


class ManualSource:
    """Queue for human-injected signals (via Telegram /launch <TICKER> or CLI).
    These carry high magnitude so an operator can force a candidate through the
    normal scoring + approval flow rather than bypassing it.
    """

    name = "manual"

    def __init__(self) -> None:
        self._queue: list[Signal] = []

    def push(self, ticker: str, headline: str = "", magnitude: float = 20.0) -> Signal:
        sig = Signal(
            ticker=ticker.upper(),
            headline=headline or f"manual push: {ticker.upper()}",
            sources=["manual", "operator"],
            meta={"magnitude": magnitude},
        )
        self._queue.append(sig)
        return sig

    async def poll(self) -> list[Signal]:
        out, self._queue = self._queue, []
        return out


# ---------------------------------------------------------------------------
# Real attention source: financial news volume via Google News RSS.
# ---------------------------------------------------------------------------

_GOOGLE_NEWS_RSS = (
    "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
)
# A browser-ish UA — Google News RSS returns empty/blocked for some default agents.
_UA = "Mozilla/5.0 (compatible; StockForge/0.1; +https://github.com/PhantomCapAI)"


class _Item:
    __slots__ = ("title", "outlet", "published")

    def __init__(self, title: str, outlet: str, published: datetime | None):
        self.title = title
        self.outlet = outlet
        self.published = published


class NewsRssSource:
    """Real attention source — measures how much fresh financial news each
    watchlist ticker is generating, using Google News RSS (no API key, no auth).

    Honest mapping to the scorer's inputs:
    - ``sources`` = the distinct news outlets covering the ticker in the window
      (real breadth: more independent outlets ⇒ more genuine attention).
    - ``meta['magnitude']`` = count of fresh articles, capped at 20 (volume).
    - ``headline`` = the freshest article that carries the most narrative
      keywords, so the scorer's keyword bonus reflects real coverage.

    A ticker with no fresh news emits no signal (silence ≠ attention). This is
    what lets a real narrative cross ``min_attention_score`` while the baseline
    watchlist stays below it.
    """

    name = "news-rss"

    def __init__(
        self,
        tickers: list[str],
        *,
        freshness_hours: int = 24,
        timeout: float = 8.0,
        max_concurrency: int = 4,
    ):
        self.tickers = tickers
        self.freshness = timedelta(hours=freshness_hours)
        self.timeout = timeout
        self._sem = asyncio.Semaphore(max_concurrency)

    async def poll(self) -> list[Signal]:
        headers = {"User-Agent": _UA}
        # Separate connect/read budgets: cold connects to Google News are the
        # main source of transient timeouts, so give connect a little room.
        timeout = httpx.Timeout(self.timeout, connect=self.timeout + 4.0)
        async with httpx.AsyncClient(
            timeout=timeout, headers=headers, follow_redirects=True
        ) as client:
            results = await asyncio.gather(
                *(self._poll_ticker(client, t) for t in self.tickers),
                return_exceptions=True,
            )
        signals: list[Signal] = []
        for t, r in zip(self.tickers, results, strict=False):
            if isinstance(r, Exception):
                log.warning("news source failed for %s: %r", t, r)
            elif r is not None:
                signals.append(r)
        return signals

    async def _poll_ticker(self, client: httpx.AsyncClient, ticker: str) -> Signal | None:
        # `when:1d` scopes Google News to the last day; we still filter by pubDate.
        url = _GOOGLE_NEWS_RSS.format(query=quote_plus(f'"{ticker}" stock when:1d'))
        async with self._sem:
            xml_text = await _fetch(client, url)
        return self.parse(ticker, xml_text, now=datetime.now(UTC))

    # -- pure parsing (no network) — unit-testable ---------------------------
    def parse(self, ticker: str, xml_text: str, *, now: datetime | None = None) -> Signal | None:
        now = now or datetime.now(UTC)
        items = _parse_rss_items(xml_text)
        fresh = [it for it in items if it.published and (now - it.published) <= self.freshness]
        if not fresh:
            return None
        outlets = sorted({it.outlet for it in fresh if it.outlet})
        best = _pick_headline(fresh)
        magnitude = min(20.0, float(len(fresh)))
        return Signal(
            ticker=ticker.upper(),
            headline=best,
            sources=outlets or ["news"],
            meta={
                "magnitude": magnitude,
                "fresh_count": len(fresh),
                "outlets": outlets,
                "provider": "google-news-rss",
            },
        )


def _parse_rss_items(xml_text: str) -> list[_Item]:
    """Extract (title, outlet, published) from an RSS document. Tolerant of
    malformed/empty feeds — returns [] rather than raising."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out: list[_Item] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        source_el = item.find("source")
        outlet = (source_el.text or "").strip() if source_el is not None else ""
        # Google News titles are usually "Headline - Outlet"; prefer a clean
        # headline and fall back to the outlet from the suffix when <source> is absent.
        if not outlet and " - " in title:
            title, _, outlet = title.rpartition(" - ")
            title, outlet = title.strip(), outlet.strip()
        published = _parse_date(item.findtext("pubDate"))
        out.append(_Item(title, outlet, published))
    return out


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    # Normalize to aware UTC so comparisons never blow up on naive datetimes.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _pick_headline(items: list[_Item]) -> str:
    """The freshest headline carrying the most narrative keywords."""

    def key(it: _Item) -> tuple[int, float]:
        hits = sum(1 for w in NARRATIVE_WORDS if w in it.title.lower())
        ts = it.published.timestamp() if it.published else 0.0
        return (hits, ts)

    return max(items, key=key).title
