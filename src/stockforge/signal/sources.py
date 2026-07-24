"""Signal sources.

A SignalSource yields candidate Signals. Phase 3 ships two safe, no-external-key
sources; real sources (X/Twitter trends, news APIs, on-chain flow) implement the
same `poll()` contract and drop in without orchestrator changes.
"""

from __future__ import annotations

from typing import Protocol

from ..logging import get_logger
from ..models import Signal

log = get_logger("signal.sources")


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
