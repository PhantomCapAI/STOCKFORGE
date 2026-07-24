"""Attention signal detection + scoring."""

from .scorer import AttentionScorer
from .sources import ManualSource, SignalSource, WatchlistHeuristicSource

__all__ = ["AttentionScorer", "SignalSource", "WatchlistHeuristicSource", "ManualSource"]
