"""Elon-tweet source — ticker resolution, 'hit the mark' logic, parsing, poll.
All offline (inbox provider / static payloads)."""

from __future__ import annotations

from stockforge.signal import (
    ElonTweetSource,
    TweetInbox,
    evaluate_tweet,
    resolve_ticker,
)
from stockforge.signal.elon import DEFAULT_TICKER_MAP, Tweet, parse_x_api

TMAP = DEFAULT_TICKER_MAP


def _tw(text, likes=0, rts=0):
    return Tweet(id="t1", text=text, like_count=likes, retweet_count=rts)


# -- ticker resolution ------------------------------------------------------


def test_cashtag_beats_name():
    assert resolve_ticker("thinking about $NVDA and tesla", TMAP) == "NVDA"


def test_company_name_resolves():
    assert resolve_ticker("Tesla production is insane", TMAP) == "TSLA"
    assert resolve_ticker("Dogecoin to the moon", TMAP) == "DOGE"


def test_no_ticker_returns_empty():
    assert resolve_ticker("great day for humanity", TMAP) == ""


# -- hit the mark -----------------------------------------------------------


def test_no_ticker_misses():
    r = evaluate_tweet(_tw("love rockets", likes=999999), ticker_map=TMAP, min_engagement=10000)
    assert r.hit is False


def test_ticker_but_low_engagement_no_hype_misses():
    r = evaluate_tweet(_tw("Tesla", likes=10, rts=0), ticker_map=TMAP, min_engagement=10000)
    assert r.hit is False


def test_ticker_with_hype_hits_even_if_low_engagement():
    r = evaluate_tweet(_tw("Tesla to the moon, record quarter", likes=5), ticker_map=TMAP, min_engagement=10000)
    assert r.hit is True
    assert r.ticker == "TSLA"
    assert r.magnitude > 0


def test_cashtag_hits_and_bumps_magnitude():
    r = evaluate_tweet(_tw("$TSLA", likes=0), ticker_map=TMAP, min_engagement=10000)
    assert r.hit is True and r.ticker == "TSLA"


def test_high_engagement_hits_and_scales_magnitude():
    hi = evaluate_tweet(_tw("Tesla", likes=500000, rts=100000), ticker_map=TMAP, min_engagement=10000)
    lo = evaluate_tweet(_tw("Tesla squeeze", likes=12000), ticker_map=TMAP, min_engagement=10000)
    assert hi.hit and lo.hit
    assert hi.magnitude >= lo.magnitude
    assert hi.magnitude <= 20.0  # capped to what the scorer accepts


# -- X API parsing ----------------------------------------------------------


def test_parse_x_api_payload():
    payload = {
        "data": [
            {
                "id": "1",
                "text": "Tesla record earnings",
                "created_at": "2026-07-24T10:00:00.000Z",
                "public_metrics": {"like_count": 120000, "retweet_count": 8000},
            }
        ]
    }
    tweets = parse_x_api(payload)
    assert len(tweets) == 1
    assert tweets[0].like_count == 120000
    assert tweets[0].engagement == 120000 + 2 * 8000


def test_parse_x_api_empty_is_safe():
    assert parse_x_api({}) == []


# -- source poll via inbox (no network) -------------------------------------


async def test_source_emits_only_for_hits():
    inbox = TweetInbox()
    inbox.push("Tesla to the moon record breakout", like_count=200000)  # hit
    inbox.push("beautiful sunset today", like_count=999999)  # no ticker -> miss
    src = ElonTweetSource(inbox)
    sigs = await src.poll()
    assert len(sigs) == 1
    s = sigs[0]
    assert s.ticker == "TSLA"
    assert s.sources == ["elon", "x"]
    assert s.meta["magnitude"] > 0
    assert "tweet_id" in s.meta


async def test_source_sets_tweet_url():
    inbox = TweetInbox()
    inbox.push("$TSLA squeeze", tweet_id="1234567890")
    src = ElonTweetSource(inbox, handle="elonmusk")
    sigs = await src.poll()
    assert sigs[0].meta["tweet_url"] == "https://x.com/elonmusk/status/1234567890"


# -- Grok provider parsing (no network) -------------------------------------


def test_parse_grok_tweets_with_fences_and_prose():
    from stockforge.signal.elon import parse_grok_tweets

    content = (
        "Sure! Here are the tweets:\n```json\n"
        '{"tweets":[{"id":"1","text":"Dogecoin to the moon","like_count":300000,'
        '"retweet_count":40000},{"id":"2","text":"","like_count":0,"retweet_count":0}]}'
        "\n```"
    )
    tweets = parse_grok_tweets(content)
    assert len(tweets) == 1  # empty-text row dropped
    assert tweets[0].text == "Dogecoin to the moon"
    assert tweets[0].like_count == 300000


def test_parse_grok_tweets_garbage_is_safe():
    from stockforge.signal.elon import parse_grok_tweets

    assert parse_grok_tweets("no json here") == []
