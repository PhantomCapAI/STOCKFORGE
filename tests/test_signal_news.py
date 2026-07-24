"""NewsRssSource — parsing + scoring, all offline (no network)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from stockforge.signal import AttentionScorer, NewsRssSource

NOW = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC)


def _rss(items: str) -> str:
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{items}</channel></rss>'


def _item(title: str, outlet: str, dt: datetime, with_source: bool = True) -> str:
    src = f'<source url="https://x">{outlet}</source>' if with_source else ""
    return (
        f"<item><title>{title}</title>"
        f"<pubDate>{dt.strftime('%a, %d %b %Y %H:%M:%S %z')}</pubDate>"
        f"{src}</item>"
    )


def test_parse_extracts_outlets_headline_and_magnitude():
    xml = _rss(
        _item("NVDA rips to record high on AI demand", "Reuters", NOW - timedelta(hours=1))
        + _item("Nvidia earnings beat", "Bloomberg", NOW - timedelta(hours=3))
        + _item("Analysts raise NVDA guidance", "CNBC", NOW - timedelta(hours=5))
    )
    sig = NewsRssSource(["NVDA"]).parse("NVDA", xml, now=NOW)
    assert sig is not None
    assert sig.ticker == "NVDA"
    # distinct outlets become the (real) breadth signal
    assert set(sig.sources) == {"Reuters", "Bloomberg", "CNBC"}
    assert sig.meta["fresh_count"] == 3
    assert sig.meta["magnitude"] == 3.0
    # freshest, most-narrative headline is chosen (has "record high")
    assert "record" in sig.headline.lower()


def test_stale_items_are_dropped():
    xml = _rss(_item("NVDA old news", "Reuters", NOW - timedelta(hours=48)))
    assert NewsRssSource(["NVDA"], freshness_hours=24).parse("NVDA", xml, now=NOW) is None


def test_no_items_returns_none():
    assert NewsRssSource(["NVDA"]).parse("NVDA", _rss(""), now=NOW) is None


def test_outlet_fallback_from_title_suffix():
    # No <source> element -> outlet parsed from "Headline - Outlet"
    xml = _rss(_item("GME squeeze resumes - MarketWatch", "", NOW, with_source=False))
    sig = NewsRssSource(["GME"]).parse("GME", xml, now=NOW)
    assert sig is not None
    assert sig.sources == ["MarketWatch"]
    assert sig.headline == "GME squeeze resumes"


def test_malformed_feed_is_safe():
    assert NewsRssSource(["NVDA"]).parse("NVDA", "<not-xml", now=NOW) is None


def test_real_news_can_cross_threshold_while_baseline_cannot():
    """The whole point: genuine coverage scores high enough to be eligible."""
    scorer = AttentionScorer()
    xml = _rss(
        _item("NVDA surge to record all-time high on breakout", "Reuters", NOW - timedelta(hours=1))
        + _item("Nvidia rally continues after earnings", "Bloomberg", NOW - timedelta(hours=2))
        + _item("NVDA squeeze talk grows", "CNBC", NOW - timedelta(hours=2))
        + _item("Nvidia guidance raised", "WSJ", NOW - timedelta(hours=3))
        + _item("NVDA moon watch", "Barrons", NOW - timedelta(hours=4))
    )
    sig = scorer.enrich(NewsRssSource(["NVDA"]).parse("NVDA", xml, now=NOW))
    assert sig.attention_score >= 65  # crosses the default gate

    # A baseline watchlist ping (no news, magnitude 0) stays well below it.
    from stockforge.models import Signal

    baseline = scorer.enrich(
        Signal(ticker="NVDA", headline="NVDA baseline watch", sources=["watchlist"], meta={"magnitude": 0.0})
    )
    assert baseline.attention_score < 65
