"""Attention signal detection + scoring."""

from .elon import (
    ElonTweetSource,
    Tweet,
    TweetInbox,
    XApiTweetProvider,
    evaluate_tweet,
    resolve_ticker,
)
from .scorer import AttentionScorer
from .sources import ManualSource, NewsRssSource, SignalSource, WatchlistHeuristicSource

__all__ = [
    "AttentionScorer",
    "SignalSource",
    "WatchlistHeuristicSource",
    "ManualSource",
    "NewsRssSource",
    "ElonTweetSource",
    "TweetInbox",
    "XApiTweetProvider",
    "Tweet",
    "evaluate_tweet",
    "resolve_ticker",
]
