"""Attention signal detection + scoring."""

from .scorer import AttentionScorer
from .sources import ManualSource, NewsRssSource, SignalSource, WatchlistHeuristicSource

__all__ = [
    "AttentionScorer",
    "SignalSource",
    "WatchlistHeuristicSource",
    "ManualSource",
    "NewsRssSource",
]
