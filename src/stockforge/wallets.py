"""Multi-wallet support — ONE honest operation, several wallets.

Why multiple wallets (all legitimate, all disclosed as the same operator):
  * Key segregation / opsec — a compromised hot key exposes one wallet, not all.
  * Treasury management — split fee inflows across addresses.
  * Reduced single-point-of-failure.
  * Respect each wallet's OWN Bankr rate limit (50/100 per day, 1/min) instead of
    piling everything onto one address.

Explicitly NOT for: pretending the wallets are unrelated/independent creators.
There is no disguise here — attribution is tracked and reported openly, and each
wallet is part of the same system. Scaling *aggregate* launch volume across many
wallets is still subject to Bankr's ToS + anti-spam rules; Bankr's documented
path for genuine high-volume use is a support ticket, not more wallets.

Each wallet enforces Bankr's per-wallet caps independently, AND a single global
daily budget caps the whole operation so it can never run away regardless of how
many wallets are configured.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .config import Settings
from .db import Store
from .logging import get_logger
from .ratelimit import LaunchRateLimiter

log = get_logger("wallets")


@dataclass
class Wallet:
    """One launching identity in the operation. `fee_recipient` is where this
    wallet's creator fees accrue — defaults to the main treasury so fees
    consolidate. `api_key`/`private_key` are optional per-wallet Bankr
    credentials (a real distinct launching identity on the REST backend)."""

    id: str
    fee_recipient: str
    api_key: str = ""
    private_key: str = ""
    is_club: bool = False

    def counter(self) -> str:
        return f"launch:{self.id}"


@dataclass
class WalletPool:
    wallets: list[Wallet]
    main_treasury: str
    _limiters: dict[str, LaunchRateLimiter] = field(default_factory=dict, repr=False)

    @classmethod
    def from_settings(cls, settings: Settings) -> WalletPool:
        """Build the pool from STOCKFORGE_WALLETS (JSON list) if present, else a
        single 'main' wallet derived from the treasury/beneficiary. Fully
        backward-compatible: no config change ⇒ single-wallet behavior."""
        treasury = settings.treasury
        raw = (settings.wallets_json or "").strip()
        wallets: list[Wallet] = []
        if raw:
            try:
                for i, w in enumerate(json.loads(raw)):
                    wid = str(w.get("id") or f"w{i}")
                    wallets.append(
                        Wallet(
                            id=wid,
                            fee_recipient=w.get("fee_recipient") or treasury,
                            api_key=w.get("api_key", ""),
                            private_key=w.get("private_key", ""),
                            is_club=bool(w.get("club", False)),
                        )
                    )
            except (json.JSONDecodeError, TypeError, AttributeError) as e:
                log.error("STOCKFORGE_WALLETS is not valid JSON (%s); using single wallet", e)
                wallets = []
        if not wallets:
            wallets = [
                Wallet(
                    id="main",
                    fee_recipient=treasury,
                    api_key=settings.bankr_api_key,
                    private_key=settings.bankr_private_key,
                )
            ]
        return cls(wallets=wallets, main_treasury=treasury)

    def by_id(self, wallet_id: str) -> Wallet | None:
        return next((w for w in self.wallets if w.id == wallet_id), None)

    def limiter(self, wallet: Wallet, store: Store, per_wallet_cap: int) -> LaunchRateLimiter:
        rl = self._limiters.get(wallet.id)
        if rl is None:
            rl = LaunchRateLimiter(
                store,
                daily_budget=per_wallet_cap,
                is_club=wallet.is_club,
                counter_name=wallet.counter(),
            )
            self._limiters[wallet.id] = rl
        return rl

    async def select(
        self, store: Store, *, global_budget: int, per_wallet_cap: int
    ) -> tuple[Wallet, LaunchRateLimiter] | None:
        """Pick the next wallet to launch from, distributing load least-recently-
        used. Returns None if the global budget is spent or every wallet is in
        cooldown / at its cap. Enforces BOTH the per-wallet Bankr limits and the
        global operation ceiling — both hard."""
        # Global hard ceiling across all wallets (operation-wide runaway guard).
        used_global = await store.get_daily_counter("launch_all")
        if used_global >= global_budget:
            log.debug("global daily budget reached (%d/%d)", used_global, global_budget)
            return None

        eligible: list[tuple[float, Wallet, LaunchRateLimiter]] = []
        for w in self.wallets:
            rl = self.limiter(w, store, per_wallet_cap)
            dec = await rl.check()
            if dec.allowed:
                last = float(await store.kv_get(f"last:{w.counter()}", "0"))
                eligible.append((last, w, rl))
        if not eligible:
            return None
        eligible.sort(key=lambda x: x[0])  # least-recently-used wallet first
        _, wallet, rl = eligible[0]
        return wallet, rl

    async def record_launch(self, store: Store, wallet: Wallet, rl: LaunchRateLimiter) -> None:
        """Count the attempt against BOTH the wallet's limiter and the global
        operation counter (Bankr counts failures too, so call on every attempt)."""
        await rl.record()
        await store.incr_daily_counter("launch_all")

    def redacted(self) -> list[dict]:
        """Secret-free view for logs/status: ids + fee recipients, keys masked."""
        return [
            {
                "id": w.id,
                "fee_recipient": w.fee_recipient or "unset",
                "api_key": "set" if w.api_key else "unset",
                "private_key": "set" if w.private_key else "unset",
                "club": w.is_club,
            }
            for w in self.wallets
        ]
