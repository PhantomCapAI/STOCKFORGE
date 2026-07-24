"""Attention scoring (0-100).

Phase 3 keeps this intentionally simple and transparent — a weighted blend of
signals a source can plausibly provide. Swap in a real model later without
touching the orchestrator: the contract is just `score(signal) -> float`.
"""

from __future__ import annotations

from ..models import Signal

# Tickers that reliably carry retail/narrative energy get a small prior.
_HOT_PRIOR = {
    "NVDA": 12,
    "GME": 15,
    "TSLA": 12,
    "HOOD": 10,
    "SPY": 6,
    "AMD": 8,
    "PLTR": 10,
    "MSTR": 11,
}

# Words that indicate a live, tradable narrative (not just background noise).
# Public so real sources (e.g. the news source) can rank headlines by the same
# vocabulary the scorer rewards.
NARRATIVE_WORDS = (
    "surge",
    "halt",
    "squeeze",
    "earnings",
    "all-time high",
    "ath",
    "crash",
    "rally",
    "split",
    "guidance",
    "moon",
    "short",
    "meme",
    "breakout",
    "record",
)


class AttentionScorer:
    def score(self, signal: Signal) -> float:
        s = 0.0
        # Source breadth: more independent sources = more real attention.
        s += min(len(signal.sources), 5) * 8  # up to 40
        # Narrative keywords in the headline.
        headline = signal.headline.lower()
        hits = sum(1 for w in NARRATIVE_WORDS if w in headline)
        s += min(hits, 4) * 9  # up to 36
        # Ticker prior.
        s += _HOT_PRIOR.get(signal.ticker.upper(), 4)
        # Explicit external magnitude (e.g. % move, mention volume) if provided.
        s += min(float(signal.meta.get("magnitude", 0.0)), 20.0)
        return max(0.0, min(100.0, s))

    def enrich(self, signal: Signal) -> Signal:
        signal.attention_score = self.score(signal)
        return signal
