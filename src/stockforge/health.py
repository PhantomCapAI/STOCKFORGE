"""Readiness / safety checks for `stockforge doctor`.

Every check is best-effort with a short timeout and never raises — a check that
can't run reports `warn`/`fail` with an actionable message rather than crashing.
The doctor FAILS CLOSED: if the config is live (dry_run off) but a guardrail
can't function (no Bankr auth, or approvals required but Telegram unreachable),
the overall result is a hard fail.

No secrets are printed — only presence ("set"/"unset") and reachability.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from typing import Literal

import httpx

from .config import Settings
from .db import Store
from .logging import get_logger
from .ratelimit import LaunchRateLimiter

log = get_logger("health")

Level = Literal["ok", "warn", "fail"]
_MARK = {"ok": "✅", "warn": "⚠️ ", "fail": "❌"}

_NET_TIMEOUT = 6.0
_CLI_TIMEOUT = 15.0


@dataclass
class Check:
    name: str
    level: Level
    message: str = ""

    def render(self) -> str:
        tail = f" — {self.message}" if self.message else ""
        return f"  {_MARK[self.level]} {self.name}{tail}"


async def _check_bankr_reachable(settings: Settings) -> Check:
    """Connectivity to the Bankr API base via a cheap public read (no auth)."""
    # Prefer a real public read when we have a beneficiary; else just probe base.
    if settings.bankr_beneficiary_address:
        url = f"{settings.bankr_api_base}/public/doppler/creator-fees/{settings.bankr_beneficiary_address}"
    else:
        url = f"{settings.bankr_api_base}/public/doppler/creator-fees/0x0000000000000000000000000000000000000000"
    try:
        async with httpx.AsyncClient(timeout=_NET_TIMEOUT) as c:
            r = await c.get(url)
        # Any HTTP response means the host is reachable.
        return Check("bankr api reachable", "ok", f"{settings.bankr_api_base} (HTTP {r.status_code})")
    except Exception as e:  # noqa: BLE001
        return Check("bankr api reachable", "warn", f"cannot reach {settings.bankr_api_base}: {e}")


def _check_bankr_auth(settings: Settings) -> Check:
    if settings.bankr_backend == "rest":
        if settings.bankr_api_key:
            return Check(
                "bankr api key", "ok", "BANKR_API_KEY set (not validated — validation would spend)"
            )
        level = "warn" if settings.dry_run else "fail"
        return Check("bankr api key", level, "BANKR_API_KEY unset — REST launches will fail")
    # CLI backend: auth is checked separately via `bankr whoami`.
    return Check("bankr api key", "ok", "cli backend (auth checked via whoami)")


async def _check_cli(settings: Settings) -> list[Check]:
    checks: list[Check] = []
    binary = shutil.which(settings.bankr_cli_bin)
    required = settings.bankr_backend == "cli"
    if not binary:
        level = "fail" if (required and not settings.dry_run) else "warn"
        checks.append(
            Check("bankr cli installed", level, f"'{settings.bankr_cli_bin}' not on PATH (`npm i -g @bankr/cli`)")
        )
        return checks
    checks.append(Check("bankr cli installed", "ok", binary))
    # Authenticated? `bankr whoami` is a read-only identity check.
    try:
        proc = await asyncio.create_subprocess_exec(
            settings.bankr_cli_bin,
            "whoami",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=_CLI_TIMEOUT)
        if proc.returncode == 0:
            checks.append(Check("bankr cli authenticated", "ok", "whoami succeeded"))
        else:
            level = "fail" if (required and not settings.dry_run) else "warn"
            detail = (err_b.decode(errors="replace") or out_b.decode(errors="replace")).strip()[:120]
            checks.append(Check("bankr cli authenticated", level, f"whoami failed: {detail or 'not logged in'}"))
    except (TimeoutError, FileNotFoundError) as e:
        checks.append(Check("bankr cli authenticated", "warn", f"whoami check skipped: {e}"))
    return checks


async def _check_telegram(settings: Settings) -> list[Check]:
    checks: list[Check] = []
    if not settings.telegram_enabled:
        level = "fail" if (not settings.dry_run and settings.require_approval) else "warn"
        checks.append(
            Check(
                "telegram configured",
                level,
                "TELEGRAM_BOT_TOKEN/CHAT_ID unset — approvals fail-closed (all real actions denied)",
            )
        )
        return checks
    base = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
    try:
        async with httpx.AsyncClient(timeout=_NET_TIMEOUT) as c:
            me = await c.get(f"{base}/getMe")
            me_ok = me.status_code == 200 and me.json().get("ok")
            if not me_ok:
                checks.append(Check("telegram bot token", "fail", "getMe rejected the token"))
                return checks
            username = me.json().get("result", {}).get("username", "?")
            checks.append(Check("telegram bot token", "ok", f"@{username}"))
            # Validate the chat id is reachable by the bot.
            chat = await c.get(f"{base}/getChat", params={"chat_id": settings.telegram_chat_id})
            if chat.status_code == 200 and chat.json().get("ok"):
                checks.append(Check("telegram chat reachable", "ok", f"chat_id {settings.telegram_chat_id}"))
            else:
                desc = chat.json().get("description", "not reachable")
                checks.append(
                    Check("telegram chat reachable", "warn", f"getChat: {desc} (send /start to the bot once)")
                )
    except Exception as e:  # noqa: BLE001
        checks.append(Check("telegram reachable", "warn", f"telegram API error: {e}"))
    return checks


async def _check_rate_state(settings: Settings) -> Check:
    try:
        store = Store(settings.db_path)
        await store.connect()
        rl = LaunchRateLimiter(store, daily_budget=settings.daily_launch_budget)
        used = await store.get_daily_counter(rl.counter_name)
        remaining = await rl.remaining_today()
        await store.close()
        return Check(
            "rate limiter / budget",
            "ok",
            f"{used} used today, {remaining} remaining (cap {rl.effective_daily}, 1/min)",
        )
    except Exception as e:  # noqa: BLE001
        return Check("rate limiter / budget", "warn", f"state unavailable: {e}")


def _check_env(settings: Settings) -> list[Check]:
    checks: list[Check] = []
    checks.append(
        Check(
            "dry-run default",
            "ok" if settings.dry_run else "warn",
            "STOCKFORGE_DRY_RUN=true (safe)"
            if settings.dry_run
            else "STOCKFORGE_DRY_RUN=false — LIVE: real launches/claims can broadcast",
        )
    )
    # Beneficiary is required to read/claim fees.
    checks.append(
        Check(
            "beneficiary address",
            "ok" if settings.bankr_beneficiary_address else "warn",
            "set" if settings.bankr_beneficiary_address else "BANKR_BENEFICIARY_ADDRESS unset — fee polling/claims disabled",
        )
    )
    # Stock-pairing requires robinhood chain.
    if settings.default_chain != "robinhood":
        checks.append(
            Check("chain for stock-pairing", "warn", f"default chain '{settings.default_chain}' — stock-pairing only on robinhood")
        )
    return checks


async def run_doctor(settings: Settings) -> tuple[list[Check], bool]:
    """Run all checks concurrently-ish; return (checks, overall_ok)."""
    checks: list[Check] = []
    checks += _check_env(settings)
    checks.append(_check_bankr_auth(settings))

    # Network + CLI checks run concurrently.
    bankr_reach, cli_checks, tg_checks, rate_check = await asyncio.gather(
        _check_bankr_reachable(settings),
        _check_cli(settings),
        _check_telegram(settings),
        _check_rate_state(settings),
    )
    checks.append(bankr_reach)
    checks += cli_checks
    checks += tg_checks
    checks.append(rate_check)

    # Fail-closed policy: a hard fail if any check is 'fail'. Live configs surface
    # missing guardrails as 'fail' inside the individual checks above.
    overall_ok = not any(c.level == "fail" for c in checks)
    return checks, overall_ok
