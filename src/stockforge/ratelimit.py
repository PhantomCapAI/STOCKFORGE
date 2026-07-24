"""Rate limiting that mirrors Bankr's real limits + our own daily budget.

Bankr (verified from docs.bankr.bot/token-launching/overview):
  * Standard: 50 token deploys / trailing 24h
  * Bankr Club: 100 / trailing 24h
  * At most 1 deploy / minute
  * FAILED attempts still count against the daily cap

We enforce the stricter of {Bankr cap, our own STOCKFORGE_DAILY_LAUNCH_BUDGET}.
Counters are persisted so restarts don't reset the budget.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from .db import Store
from .logging import get_logger

log = get_logger("ratelimit")

BANKR_STANDARD_DAILY = 50
BANKR_CLUB_DAILY = 100
MIN_SECONDS_BETWEEN_LAUNCHES = 60  # 1 / minute


@dataclass
class RateDecision:
    allowed: bool
    reason: str = ""
    retry_after: float = 0.0  # seconds


class LaunchRateLimiter:
    """Gate for launch attempts. `check()` is read-only; call `record()` on every
    attempt (including failures) to keep counters honest."""

    def __init__(
        self,
        store: Store,
        daily_budget: int,
        is_club: bool = False,
        counter_name: str = "launch_attempts",
    ):
        self.store = store
        self.is_club = is_club
        bankr_cap = BANKR_CLUB_DAILY if is_club else BANKR_STANDARD_DAILY
        self.effective_daily = min(daily_budget, bankr_cap)
        self.counter_name = counter_name
        self._lock = asyncio.Lock()

    async def _last_launch_ts(self) -> float:
        return float(await self.store.kv_get(f"last:{self.counter_name}", "0"))

    async def check(self) -> RateDecision:
        used = await self.store.get_daily_counter(self.counter_name)
        if used >= self.effective_daily:
            return RateDecision(False, f"daily budget reached ({used}/{self.effective_daily})")
        last = await self._last_launch_ts()
        elapsed = time.time() - last
        if last and elapsed < MIN_SECONDS_BETWEEN_LAUNCHES:
            wait = MIN_SECONDS_BETWEEN_LAUNCHES - elapsed
            return RateDecision(False, "1/min cooldown", retry_after=wait)
        return RateDecision(True)

    async def record(self) -> int:
        """Count an attempt (success OR failure — mirrors Bankr's accounting)."""
        async with self._lock:
            await self.store.kv_set(f"last:{self.counter_name}", str(time.time()))
            n = await self.store.incr_daily_counter(self.counter_name)
            log.info("launch attempt %d/%d today", n, self.effective_daily)
            return n

    async def remaining_today(self) -> int:
        used = await self.store.get_daily_counter(self.counter_name)
        return max(0, self.effective_daily - used)


class AsyncTokenBucket:
    """Generic token bucket for pacing non-launch API calls (fee polls, prompts)."""

    def __init__(self, rate_per_sec: float, capacity: float):
        self.rate = rate_per_sec
        self.capacity = capacity
        self.tokens = capacity
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, cost: float = 1.0) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= cost:
                    self.tokens -= cost
                    return
                deficit = cost - self.tokens
                await asyncio.sleep(deficit / self.rate)
