from stockforge.models import Concept, LaunchResult, LaunchStatus
from stockforge.promo import Promoter


def _concept():
    return Concept(
        paired_ticker="NVDA",
        name="Silicon NVDA",
        symbol="SILNV",
        thesis="Silicon NVDA rides the NVDA compute narrative. Not affiliated with NVDA.",
        launch_tweet="$SILNV is live — riding the NVDA tape. Not affiliated with NVDA. NFA.",
    )


def _result(**kw):
    base = dict(request_id="r1", status=LaunchStatus.CONFIRMED, token_address="0xabc123", pool_url="https://pool/x")
    base.update(kw)
    return LaunchResult(**base)


def test_kit_has_oneliner_tweet_link_and_disclaimer():
    kit = Promoter().build_kit(_concept(), _result())
    assert kit.symbol == "SILNV"
    assert kit.one_liner  # non-empty narrative hook
    assert "Not affiliated with NVDA" in kit.tweet
    assert kit.launch_link == "https://pool/x"
    assert "$SILNV" in kit.hashtags


def test_kit_has_narrative_and_followups():
    kit = Promoter().build_kit(_concept(), _result())
    assert kit.narrative and "NVDA" in kit.narrative
    assert len(kit.followups) >= 2
    # A follow-up keeps the not-affiliated disclaimer.
    assert any("not affiliated" in f.lower() for f in kit.followups)


def test_render_full_includes_all_sections():
    full = Promoter().build_kit(_concept(), _result()).render_full()
    for section in ("TWEET:", "NARRATIVE:", "TAGS:", "FOLLOW-UPS"):
        assert section in full


def test_tweet_stays_within_budget():
    long = _concept()
    long.launch_tweet = "x" * 400  # force overflow
    kit = Promoter().build_kit(long, _result())
    assert len(kit.tweet) <= 270
    assert "Not affiliated with NVDA" in kit.tweet  # disclaimer preserved


def test_link_base_fallback_when_no_pool_url():
    kit = Promoter(link_base="https://dexscreener.com/base").build_kit(
        _concept(), _result(pool_url="")
    )
    assert kit.launch_link == "https://dexscreener.com/base/0xabc123"


async def test_promote_notifies_operator_only():
    sent = []

    async def notifier(msg):
        sent.append(msg)

    kit = await Promoter(notifier=notifier).promote(_concept(), _result())
    assert kit is not None
    assert len(sent) == 1
    assert "review before posting" in sent[0].lower()


async def test_promo_disabled_returns_none():
    kit = await Promoter(notifier=None, enabled=False).promote(_concept(), _result())
    assert kit is None
