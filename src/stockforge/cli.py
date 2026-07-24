"""Command-line entrypoint.

  stockforge run                 # start the autonomous loop (default)
  stockforge status              # print config + today's launch budget
  stockforge preview NVDA        # forge a concept for a ticker and preview the
                                 # exact Bankr request WITHOUT launching
  stockforge selfcheck [NVDA]    # run a full DRY-RUN pipeline end to end
  stockforge launch NVDA         # one controlled launch (respects dry-run + approval)
  stockforge fees <0xtoken>      # read fees for a token (public, no auth)
  stockforge treasury            # extracted fees + fees->compute funding status
  stockforge doctor              # environment / readiness check (fail-closed)
  stockforge preflight           # pre-live readiness checklist (fail-closed)

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
    # The CLI prints status glyphs (✅ ⚠️ ❌). On Windows the console defaults to
    # a legacy codepage (cp1252) that can't encode them, which crashes every
    # command with UnicodeEncodeError. Force UTF-8 on the standard streams.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    settings = get_settings()
    setup_logging(settings.log_level)

    parser = argparse.ArgumentParser(prog="stockforge", description="Phantom StockForge")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("run", help="start the autonomous orchestrator loop")
    sub.add_parser("status", help="print config + launch budget")
    sub.add_parser("doctor", help="check environment readiness (fail-closed)")
    sub.add_parser("preflight", help="pre-live readiness checklist (fail-closed)")
    sub.add_parser("treasury", help="show extracted fees + compute-funding status")
    p_prev = sub.add_parser("preview", help="forge + preview a launch (no broadcast)")
    p_prev.add_argument("ticker")
    p_prev.add_argument("--chain", default=None, choices=["base", "robinhood"])
    p_launch = sub.add_parser(
        "launch", help="controlled SINGLE launch (respects dry-run + approval)"
    )
    p_launch.add_argument("ticker")
    p_launch.add_argument("--chain", default=None, choices=["base", "robinhood"])
    p_self = sub.add_parser("selfcheck", help="run a full dry-run pipeline end to end")
    p_self.add_argument("ticker", nargs="?", default="NVDA")
    p_self.add_argument("--chain", default=None, choices=["base", "robinhood"])
    p_self.add_argument(
        "--live-approval",
        action="store_true",
        help="actually send a Telegram approval prompt and wait (bounded) to verify buttons",
    )
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
            return asyncio.run(_doctor())
        if cmd == "preflight":
            return asyncio.run(_preflight())
        if cmd == "treasury":
            return asyncio.run(_treasury())
        if cmd == "preview":
            return asyncio.run(_preview(args.ticker, args.chain))
        if cmd == "launch":
            return asyncio.run(_launch_once(args.ticker, args.chain))
        if cmd == "selfcheck":
            return asyncio.run(_selfcheck(args.ticker, args.chain, args.live_approval))
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


def _quiet_logs() -> None:
    """Silence incidental INFO logs (e.g. DB 'state store ready') so the read-only
    checklist output stays clean. The checks print their own status lines."""
    import logging

    logging.getLogger("stockforge").setLevel(logging.WARNING)


_BAR = "  " + "─" * 54


async def _doctor() -> int:
    from .health import run_doctor

    _quiet_logs()
    settings = get_settings()
    print("== StockForge doctor ==")
    print(f"  backend={settings.bankr_backend} chain={settings.default_chain} "
          f"dry_run={settings.dry_run} require_approval={settings.require_approval}")
    checks, ok = await run_doctor(settings)
    for c in checks:
        print(c.render())
    print(_BAR)
    if ok:
        print("  RESULT:  ✅ READY (no hard failures)")
    else:
        print("  RESULT:  ❌ NOT READY — resolve the ❌ items above before running live")
    print(_BAR)
    return 0 if ok else 1


async def _treasury() -> int:
    """Extraction view: fees claimed (local audit trail + Bankr's own totals) and
    how compute funding is wired. Read-only, no secrets, no spend."""
    from .compute import compute_funding_status
    from .db import Store
    from .fees import FeeReader

    _quiet_logs()
    settings = get_settings()
    print("== StockForge treasury / extraction ==")
    print(f"  treasury address : {settings.treasury or 'UNSET (set STOCKFORGE_TREASURY_ADDRESS or beneficiary)'}")
    print(f"  auto_claim       : {settings.auto_claim}   min_claim_weth={settings.fee_claim_min_weth}")

    from .wallets import WalletPool

    store = Store(settings.db_path)
    await store.connect()
    summary = await store.claim_summary()
    by_wallet = await store.launch_counts_by_wallet()
    per_token = await store.per_token_latest_fees()
    await store.close()

    # Wallet pool (one honest operation) + per-wallet launch attribution.
    pool = WalletPool.from_settings(settings)
    print("  -- wallets (one operation) --")
    for w in pool.redacted():
        print(f"  {w['id']:12} fees->{w['fee_recipient']}  launched={by_wallet.get(w['id'], 0)}")

    print("  -- local claim audit trail --")
    print(f"  claim attempts   : {summary['claim_attempts']}  successes={summary['claim_successes']}")
    print(f"  WETH claimed (recorded): {summary['weth_claimed_recorded']:.6f}")
    if summary["last_claim"]:
        lc = summary["last_claim"]
        print(f"  last claim       : {lc['at']} [{lc['mode']}] ok={lc['ok']} {lc['weth']:.6f} WETH")

    if per_token:
        print("  -- per-token fees (latest seen) --")
        for t in per_token[:10]:
            print(f"  {t['token'][:14]}… claimable={t['claimable_weth']} claimed={t['claimed_weth']}")

    if settings.treasury:
        try:
            async with FeeReader(settings.bankr_api_base) as reader:
                totals = await reader.creator_totals(settings.treasury)
            print("  -- Bankr creator-fees (authoritative) --")
            if totals:
                print(f"  lifetime WETH    : {totals.get('lifetime_weth', 0)}")
                print(f"  claimable WETH   : {totals.get('claimable_weth', 0)}")
                print(f"  claimed WETH     : {totals.get('claimed_weth', 0)}")
                print(f"  tokens           : {totals.get('token_count', 0)}")
            else:
                print("  (no data / endpoint unreachable from here)")
        except Exception as e:  # noqa: BLE001
            print(f"  (creator-fees read failed: {e})")

    cf = compute_funding_status(settings)
    print("  -- fees -> compute (Bankr LLM Gateway) --")
    print(f"  loop        : {cf['loop']}")
    print(f"  llm_gateway : {cf['llm_gateway']}")
    print(f"  top up      : {cf['commands']['top_up']}  |  auto: {cf['commands']['auto_top_up']}")
    return 0


async def _preflight() -> int:
    from .health import run_preflight

    _quiet_logs()
    settings = get_settings()
    print("== StockForge preflight — are you ready to turn dry-run OFF? ==")
    print(f"  backend={settings.bankr_backend} chain={settings.default_chain} "
          f"dry_run={settings.dry_run} require_approval={settings.require_approval}\n")
    checks, ready = await run_preflight(settings)
    for i, c in enumerate(checks, 1):
        print(f"  {i:>2}. {c.render().strip()}")
    print(f"\n{_BAR}")
    if ready:
        print("  RESULT:  ✅ READY FOR LIVE")
        print(_BAR)
        print("  Still required MANUALLY before flipping dry-run off:")
        print("    • verify ONE stock-paired launch on Bankr yourself (pair_status)")
        print("    • set STOCKFORGE_DAILY_LAUNCH_BUDGET=1")
    else:
        print("  RESULT:  ⛔ NOT READY FOR LIVE")
        print(_BAR)
        print("  Resolve every ⚠️/❌ item above. Dry-run stays ON until all are green.")
        print("  This is by design — fail-closed.")
    return 0 if ready else 1


async def _launch_once(ticker: str, chain: str | None) -> int:
    """Controlled single launch. Respects dry-run (default) and, when live,
    Telegram approval. Logs the exact prompt and records the pair outcome."""
    from .models import Signal
    from .orchestrator import Orchestrator
    from .signal import AttentionScorer

    settings = get_settings()
    if chain:
        settings.default_chain = chain
    ticker = ticker.upper()
    mode = "DRY-RUN (nothing broadcasts)" if settings.dry_run else "LIVE"
    print(f"== Controlled single launch — {ticker} on {settings.default_chain} [{mode}] ==")

    orch = Orchestrator(settings)
    await orch.store.connect()
    try:
        scorer = AttentionScorer()
        sig = scorer.enrich(
            Signal(
                ticker=ticker,
                headline=f"{ticker} manual single launch",
                sources=["cli", "operator"],
                meta={"magnitude": 20},
            )
        )
        recent = await orch.store.recent_concept_slugs()
        concept = await orch.forge.forge(sig, recent_slugs=recent)
        if concept is None:
            print("❌ concept rejected by anti-slop; nothing launched.")
            return 1
        await orch.store.save_concept(concept)
        print(f"  concept: ${concept.symbol} '{concept.name}'")

        # For a LIVE launch that needs approval, run the Telegram poller so the
        # Approve/Reject buttons actually resolve.
        poller = None
        if not settings.dry_run and settings.require_approval and orch.tg.enabled:
            print("  waiting for Telegram approval (tap Approve/Reject)…")
            poller = asyncio.create_task(orch.tg.run())
        try:
            result = await orch.gated_launch(concept)
        finally:
            if poller is not None:
                await orch.tg.stop()
                poller.cancel()

        if result is None:
            print("  ⛔ not launched (rate-limited, or approval denied/unavailable). See logs.")
            return 1
        print(f"  status={result.status.value}  pair={result.pair_status.value}"
              f"  (requested={result.pair_requested or 'none'})")
        if result.token_address:
            print(f"  token: {result.token_address}")
        if result.error:
            print(f"  error: {result.error}")
        return 0
    finally:
        await orch.forge.aclose()
        await orch.claimer.aclose()
        await orch.store.close()


async def _selfcheck(ticker: str, chain: str | None, live_approval: bool) -> int:
    """Exercise the whole pipeline in DRY-RUN so a human can verify it end to end
    without spending a real launch: signal -> concept -> launch(dry) -> fee check
    -> approval flow. Refuses to run if dry-run is somehow off."""
    from .fees import FeeReader
    from .forge import ConceptForge
    from .launcher import BankrLauncher
    from .models import Approval, ApprovalKind, LaunchRequest
    from .orchestrator.telegram import TelegramControl
    from .signal import AttentionScorer

    settings = get_settings()
    if not settings.dry_run:
        print("❌ selfcheck refuses to run with STOCKFORGE_DRY_RUN=false (it must stay a dry run).")
        return 1
    chain = chain or settings.default_chain
    ticker = ticker.upper()
    print(f"== StockForge selfcheck (DRY-RUN) — {ticker} on {chain} ==\n")

    # 1) Signal
    scorer = AttentionScorer()
    sig = scorer.enrich(
        Signal(
            ticker=ticker,
            headline=f"{ticker} squeeze rally record earnings",
            sources=["selfcheck", "manual", "news"],
            meta={"magnitude": 18},
        )
    )
    print(f"[1/5] signal      score={sig.attention_score:.0f}/100 "
          f"(gate={settings.min_attention_score}) -> {'ELIGIBLE' if sig.attention_score >= settings.min_attention_score else 'below gate'}")

    # 2) Concept
    forge = ConceptForge(settings)
    concept = await forge.forge(sig, recent_slugs=[])
    await forge.aclose()
    if concept is None:
        print("[2/5] concept     ❌ rejected by anti-slop")
        return 1
    print(f"[2/5] concept     ${concept.symbol} '{concept.name}' unique={concept.uniqueness_score:.2f}")

    # 3) Launch (dry-run)
    req = LaunchRequest(
        concept_id=concept.id,
        name=concept.name,
        symbol=concept.symbol,
        chain=chain,
        fee_recipient=settings.bankr_beneficiary_address,
        disable_vesting=settings.disable_vesting,
        pair_with=concept.paired_ticker if chain == "robinhood" else "",
    )
    launcher = BankrLauncher(settings)
    preview = launcher.preview(req)
    print(f"[3/5] launch      prompt: {preview['prompt']}")
    result = await launcher.launch(req)
    print(f"                  status={result.status.value} pair={result.pair_status.value} "
          f"(requested={result.pair_requested or 'none'})")
    if result.status.value not in ("simulated",):
        print("                  ⚠️  expected a SIMULATED result in dry-run")

    # 4) Fee check (dry — read-only public endpoint if we have a beneficiary)
    if settings.bankr_beneficiary_address:
        try:
            async with FeeReader(settings.bankr_api_base) as reader:
                totals = await reader.creator_totals(settings.bankr_beneficiary_address)
            print(f"[4/5] fee check   creator totals: {totals or 'no data / unreachable'}")
        except Exception as e:  # noqa: BLE001
            print(f"[4/5] fee check   skipped (reader error: {e})")
    else:
        print("[4/5] fee check   skipped (no BANKR_BENEFICIARY_ADDRESS)")

    # 5) Approval flow
    summary = (
        f"[SELFCHECK] LAUNCH ${req.symbol} on {chain} pair={preview['pair_with']} "
        f"(dry-run — nothing broadcasts)"
    )
    approval = Approval(kind=ApprovalKind.LAUNCH, ref_id=req.id, summary=summary)
    tg = TelegramControl(settings.telegram_bot_token, settings.telegram_chat_id, approval_timeout=60.0)
    if live_approval and tg.enabled:
        print("[5/5] approval    sending a live Telegram approval (60s)… tap a button to verify")
        # Need the inbound poller running to receive the button tap.
        poller = asyncio.create_task(tg.run())
        try:
            decided = await tg.request_approval(approval.id, summary)
            print(f"                  operator decision: {'APPROVED' if decided else 'REJECTED/timeout'}")
        finally:
            await tg.stop()
            poller.cancel()
    else:
        gate = "ENABLED" if tg.enabled else "DISABLED (fail-closed → auto-deny)"
        print(f"[5/5] approval    Telegram {gate}; would prompt:\n                  {summary}")
        if not tg.enabled:
            print("                  (a real launch here would be DENIED — no approver reachable)")

    print("\n✅ selfcheck complete — dry-run only, no launches spent.")
    return 0


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
