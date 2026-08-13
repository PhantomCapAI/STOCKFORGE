
from stockforge.ratelimit import (
    BANKR_CLUB_DAILY,
    BANKR_STANDARD_DAILY,
    LaunchRateLimiter,
)


async def test_daily_budget_capped_by_bankr_standard(store):
    rl = LaunchRateLimiter(store, daily_budget=1000, is_club=False)
    assert rl.effective_daily == BANKR_STANDARD_DAILY


async def test_club_cap(store):
    rl = LaunchRateLimiter(store, daily_budget=1000, is_club=True)
    assert rl.effective_daily == BANKR_CLUB_DAILY


async def test_our_budget_wins_when_stricter(store):
    rl = LaunchRateLimiter(store, daily_budget=2)
    assert rl.effective_daily == 2
    assert (await rl.check()).allowed is True
    await rl.record()
    await rl.record()
    d = await rl.check()
    assert d.allowed is False
    assert "budget" in d.reason


async def test_one_per_minute_cooldown(store):
    rl = LaunchRateLimiter(store, daily_budget=50)
    await rl.record()
    d = await rl.check()
    assert d.allowed is False
    assert d.retry_after > 0


async def test_remaining(store):
    rl = LaunchRateLimiter(store, daily_budget=3)
    assert await rl.remaining_today() == 3
    await rl.record()
    assert await rl.remaining_today() == 2
