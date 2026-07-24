"""Command-line entrypoint.

  stockforge run                 # start the autonomous loop (default)
  stockforge status              # print config + today's launch budget
  stockforge preview NVDA        # forge a concept for a ticker and preview the
                                 # exact Bankr request WITHOUT launching
  stockforge fees <0xtoken>      # read fees for a token (public, no auth)
  stockforge doctor              # environment / readiness check

Also runnable as `python -m stockforge.cli ...`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from .config import get_settings
from .logging import get_logger, setup_logging
from .models import Signal

log = get_logger("cli")


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    setup_logging(settings.log_level)

    parser = argparse.ArgumentParser(prog="stockforge", description="Phantom StockForge")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("run", help="start the autonomous orchestrator loop")
    sub.add_parser("status", help="print config + launch budget")
    sub.add_parser("doctor", help="check environment readiness")
    p_prev = sub.add_parser("preview", help="forge + preview a launch (no broadcast)")
    p_prev.add_argument("ticker")
    p_prev.add_argument("--chain", default=None, choices=["base", "robinhood"])
    p_fees = sub.add_parser("fees", help="read fees for a token address")
    p_fees.add_argument("token")

    args = parser.parse_args(argv)
    cmd = args.cmd or "run"

    try:
        if cmd == "run":
            return asyncio.run(_run())
        if cmd == "status":
            return asyncio.run(_status())
        if cmd == "doctor":
            return _doctor()
        if cmd == "preview":
            return asyncio.run(_preview(args.ticker, args.chain))
        if cmd == "fees":
            return asyncio.run(_fees(args.token))
    except KeyboardInterrupt:
        log.info("interrupted")
        return 130
    parser.print_help()
    return 1


async def _run() -> int:
    from .orchestrator import Orchestrator

    orch = Orchestrator(get_settings())
    await orch.start()
    return 0


async def _status() -> int:
    from .db import Store

    settings = get_settings()
    store = Store(settings.db_path)
    await store.connect()
    used = await store.get_daily_counter("launch_attempts")
    await store.close()
    cfg = settings.redacted()
    print("== StockForge status ==")
    for k, v in cfg.items():
        print(f"  {k:22} {v}")
    print(f"  launches_today         {used}")
    return 0


def _doctor() -> int:
    settings = get_settings()
    print("== StockForge doctor ==")
    checks: list[tuple[str, bool, str]] = []
    checks.append(("dry_run safety on", settings.dry_run, "OFF — real money will move!"))
    checks.append(
        (
            "bankr auth present",
            bool(settings.bankr_api_key or settings.bankr_private_key),
            "no BANKR_API_KEY/BANKR_PRIVATE_KEY — launches will fail unless dry-run",
        )
    )
    checks.append(
        ("beneficiary set (for fees)", bool(settings.bankr_beneficiary_address), "fee polling disabled")
    )
    checks.append(("telegram configured", settings.telegram_enabled, "approvals fail-closed (deny)"))
    ok = True
    for name, passed, warn in checks:
        mark = "✅" if passed else "⚠️ "
        print(f"  {mark} {name}" + ("" if passed else f"  — {warn}"))
        # Only dry_run being OFF without approval is a hard fail worth flagging.
    if not settings.dry_run and not settings.telegram_enabled and settings.require_approval:
        print("  ❌ dry_run OFF + approvals required but Telegram not configured -> all launches denied")
        ok = False
    print("  backend:", settings.bankr_backend, "| chain:", settings.default_chain)
    return 0 if ok else 1


async def _preview(ticker: str, chain: str | None) -> int:
    from .forge import ConceptForge
    from .launcher import BankrLauncher
    from .models import LaunchRequest
    from .signal import AttentionScorer

    settings = get_settings()
    scorer = AttentionScorer()
    sig = scorer.enrich(
        Signal(ticker=ticker.upper(), headline=f"{ticker.upper()} manual preview", sources=["cli"], meta={"magnitude": 20})
    )
    forge = ConceptForge(settings)
    concept = await forge.forge(sig, recent_slugs=[])
    await forge.aclose()
    if concept is None:
        print("concept rejected by anti-slop; try again")
        return 1
    chain = chain or settings.default_chain
    req = LaunchRequest(
        concept_id=concept.id,
        name=concept.name,
        symbol=concept.symbol,
        chain=chain,
        fee_recipient=settings.bankr_beneficiary_address,
        disable_vesting=settings.disable_vesting,
        pair_with=concept.paired_ticker if chain == "robinhood" else "",
    )
    preview = BankrLauncher(settings).preview(req)
    print(f"== Concept for {ticker.upper()} (attention {sig.attention_score:.0f}/100) ==")
    print(f"  name    : {concept.name}")
    print(f"  symbol  : ${concept.symbol}")
    print(f"  unique  : {concept.uniqueness_score:.2f}")
    print(f"  thesis  : {concept.thesis}")
    print(f"  tweet   : {concept.launch_tweet}")
    print("== Launch request (NOT sent) ==")
    for k, v in preview.items():
        print(f"  {k:10}: {v}")
    return 0


async def _fees(token: str) -> int:
    from .fees import FeeReader

    settings = get_settings()
    async with FeeReader(settings.bankr_api_base) as reader:
        snap = await reader.token_fees(token)
        print(f"== Fees for {token} ==")
        print(f"  claimable WETH : {snap.claimable_weth}")
        print(f"  claimable tok  : {snap.claimable_token}")
        print(f"  lifetime WETH  : {snap.lifetime_weth}")
        print(f"  pool_id        : {snap.pool_id}")
        print(f"  initializer    : {snap.initializer}")
        if settings.bankr_beneficiary_address:
            c = await reader.claimable_for(token, settings.bankr_beneficiary_address)
            print(f"  you can claim  : {c.claimable_weth} WETH / {c.claimable_token} tok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
