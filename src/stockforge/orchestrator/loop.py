"""Main async orchestrator.

Pipeline each tick:
  poll sources -> score -> pick best eligible -> forge concept -> anti-slop
  -> rate-limit + daily-budget gate -> human approval -> Bankr launch
  -> persist -> promote.

Separately, on a slower cadence, sweep fees for launched tokens and (with
approval) claim them, closing the loop: fees -> compute.

Everything money-moving passes through: dry_run switch, circuit breaker,
rate limiter, and (optionally) Telegram approval. Fail-closed by default.
"""

from __future__ import annotations

import asyncio
import time

from ..circuit import CircuitBreaker, CircuitOpenError
from ..config import Settings
from ..db import Store
from ..fees import FeeClaimer, FeeReader
from ..forge import ConceptForge
from ..launcher import BankrLauncher
from ..logging import get_logger
from ..models import (
    Approval,
    ApprovalKind,
    Concept,
    LaunchRequest,
    LaunchResult,
    LaunchStatus,
    Signal,
)
from ..observability import (
    build_claim_record,
    build_launch_record,
    log_claim_record,
    log_launch_record,
)
from ..promo import Promoter
from ..ratelimit import LaunchRateLimiter
from ..signal import (
    AttentionScorer,
    ManualSource,
    NewsRssSource,
    WatchlistHeuristicSource,
)
from .telegram import TelegramControl

log = get_logger("orchestrator")


class Orchestrator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings.db_path)
        self.scorer = AttentionScorer()
        self.forge = ConceptForge(settings)
        self.breaker = CircuitBreaker("bankr", failure_threshold=3, reset_timeout=300)
        self.launcher = BankrLauncher(settings, breaker=self.breaker)
        self.claimer = FeeClaimer(settings)
        self.promoter: Promoter | None = None
        self.rate = LaunchRateLimiter(
            self.store, daily_budget=settings.daily_launch_budget
        )
        self.manual = ManualSource()
        # Real attention first (news), then the baseline heuristic + manual queue.
        # The heuristic keeps the pipeline exercised even when there's no news.
        self.sources: list = []
        if settings.news_source_enabled:
            self.sources.append(
                NewsRssSource(
                    settings.watchlist,
                    freshness_hours=settings.news_freshness_hours,
                )
            )
        self.sources += [WatchlistHeuristicSource(settings.watchlist), self.manual]
        self.tg = TelegramControl(settings.telegram_bot_token, settings.telegram_chat_id)
        self.promoter = Promoter(notifier=self.tg.send)
        self._paused = False
        self._stop = asyncio.Event()
        self._tick = 0

    # ---- lifecycle -----------------------------------------------------------
    async def start(self) -> None:
        await self.store.connect()
        self._register_commands()
        log.info("config: %s", self.settings.redacted())
        self._announce_mode()
        tasks = [asyncio.create_task(self._main_loop(), name="main-loop")]
        if self.tg.enabled:
            tasks.append(asyncio.create_task(self.tg.run(), name="telegram"))
        try:
            await asyncio.gather(*tasks)
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        self._stop.set()
        await self.tg.stop()
        await self.forge.aclose()
        await self.claimer.aclose()
        await self.store.close()
        log.info("shutdown complete")

    def _announce_mode(self) -> None:
        """One clear line describing exactly how the engine will behave."""
        if self.settings.dry_run:
            log.warning("DRY-RUN active — no on-chain launches or claims will broadcast")
        elif self.settings.autonomous:
            log.warning(
                "AUTONOMOUS LIVE — launches broadcast WITHOUT a Telegram tap "
                "(budget %d/day, 1/min, kill switch via /pause)",
                self.rate.effective_daily,
            )
        else:
            log.warning("LIVE — every real launch/claim requires a Telegram approval tap")
        log.info(
            "engine: continuous loop tick=%ds fee_sweep_every=%d auto_claim=%s treasury=%s",
            self.settings.tick_seconds,
            self.settings.fee_sweep_every_ticks,
            self.settings.auto_claim,
            self.settings.treasury or "unset",
        )

    # ---- main loop -----------------------------------------------------------
    async def _main_loop(self) -> None:
        sweep_every = max(1, self.settings.fee_sweep_every_ticks)
        while not self._stop.is_set():
            self._tick += 1
            try:
                if not self._paused:
                    await self._heartbeat()
                    await self._pipeline_tick()
                    if self._tick % sweep_every == 0:
                        await self._fee_sweep()
            except CircuitOpenError as e:
                log.warning("skipping tick: %s", e)
            except Exception:  # noqa: BLE001
                log.exception("tick failed (continuing)")
            await asyncio.wait(
                [asyncio.create_task(self._stop.wait())],
                timeout=self.settings.tick_seconds,
            )

    async def _heartbeat(self) -> None:
        """Terse per-tick state line so continuous operation is observable in logs."""
        remaining = await self.rate.remaining_today()
        log.info(
            "tick %d | budget %d/%d left | circuit=%s | mode=%s",
            self._tick,
            remaining,
            self.rate.effective_daily,
            self.breaker.state.value,
            "dry-run" if self.settings.dry_run
            else ("autonomous" if self.settings.autonomous else "approval"),
        )

    async def _pipeline_tick(self) -> None:
        # Fast pre-gate: if we can't launch right now (budget spent / 1-min
        # cooldown), don't spend network on signal polling + forging this tick.
        decision = await self.rate.check()
        if not decision.allowed:
            log.debug("tick skipped: %s", decision.reason)
            return

        candidates = await self._eligible_candidates()
        if not candidates:
            return

        # Try candidates best-first until one forges a clean concept AND the launch
        # path accepts it. The rate limiter (1/min) guarantees at most one real
        # launch per tick, so this just avoids wasting a whole tick when the top
        # pick is anti-slop-rejected — it does NOT bypass any limit.
        recent = await self.store.recent_concept_slugs()
        for signal in candidates:
            log.info("candidate %s score=%.0f", signal.ticker, signal.attention_score)
            await self.store.save_signal(signal)
            concept = await self.forge.forge(signal, recent_slugs=recent)
            if concept is None:
                log.info("no clean concept for %s; trying next", signal.ticker)
                continue
            await self.store.save_concept(concept)
            result = await self._attempt_launch(concept)
            # Stop once a launch was actually attempted or the limiter closed the
            # window (result is None only when gated/rejected pre-Bankr).
            if result is not None or not (await self.rate.check()).allowed:
                return

    async def _eligible_candidates(self) -> list[Signal]:
        collected: list[Signal] = []
        for src in self.sources:
            try:
                for sig in await src.poll():
                    collected.append(self.scorer.enrich(sig))
            except Exception:  # noqa: BLE001
                log.exception("source %s failed", getattr(src, "name", "?"))
        eligible = [s for s in collected if s.attention_score >= self.settings.min_attention_score]
        eligible.sort(key=lambda s: s.attention_score, reverse=True)
        return eligible

    # ---- launch --------------------------------------------------------------
    def _build_request(self, concept: Concept) -> LaunchRequest:
        return LaunchRequest(
            concept_id=concept.id,
            name=concept.name,
            symbol=concept.symbol,
            chain=self.settings.default_chain,
            image_url=concept.image_url,
            # Route creator fees to the treasury (== beneficiary unless overridden).
            fee_recipient=self.settings.treasury,
            fee_recipient_type="address" if self.settings.treasury else "",
            disable_vesting=self.settings.disable_vesting,
            # Stock-pairing intent only meaningful on Robinhood Chain (UNVERIFIED
            # capability — passed through as a hint, see launcher/base.py).
            pair_with=concept.paired_ticker if self.settings.default_chain == "robinhood" else "",
        )

    async def gated_launch(self, concept: Concept) -> LaunchResult | None:
        """The single controlled launch path: rate-limit → approval → dry-run →
        Bankr → structured record. Returns None if gated or rejected. Used by both
        the autonomous loop and the explicit one-shot CLI `stockforge launch`."""
        decision = await self.rate.check()
        if not decision.allowed:
            log.info("launch gated: %s", decision.reason)
            return None

        req = self._build_request(concept)
        preview = self.launcher.preview(req)
        summary = (
            f"LAUNCH ${req.symbol} — {req.name}\n"
            f"chain={req.chain} pair={preview['pair_with']} dry_run={self.settings.dry_run}\n"
            f"thesis: {concept.thesis[:160]}\n"
            f"prompt: {preview['prompt']}"
        )

        # Approval is required for REAL launches. Dry-run auto-proceeds (nothing
        # broadcasts) but is still fully recorded.
        approval_status = "not_required (dry-run)" if self.settings.dry_run else "auto (approval off)"
        if self.settings.require_approval and not self.settings.dry_run:
            approval = Approval(kind=ApprovalKind.LAUNCH, ref_id=req.id, summary=summary)
            await self.store.save_approval(approval)
            ok = await self.tg.request_approval(approval.id, summary)
            approval.status = "approved" if ok else "rejected"
            approval.decided_at = time.time()
            await self.store.save_approval(approval)
            approval_status = approval.status
            if not ok:
                log.info("launch %s rejected/denied by operator", req.symbol)
                # Record the denied attempt too (transparency) — no Bankr call made.
                denied = LaunchResult(request_id=req.id, status=LaunchStatus.REJECTED, error="operator denied")
                await self._record(concept, req, denied, approval_status, preview)
                return None
        else:
            await self.tg.send(f"▶️ Auto-launch (dry_run={self.settings.dry_run})\n{summary}")

        # Count the attempt BEFORE calling (Bankr counts failures too).
        await self.rate.record()
        result = await self.launcher.launch(req)
        await self._record(concept, req, result, approval_status, preview)

        pair_line = ""
        if result.pair_requested:
            pair_line = f"\npair {result.pair_requested.upper()}: {result.pair_status.value}"
        await self.tg.send(
            f"{'✅' if result.status not in (LaunchStatus.FAILED,) else '❌'} "
            f"${req.symbol} {result.status.value} {result.token_address or result.error}"
            f"{pair_line}"
        )
        return result

    async def _record(
        self,
        concept: Concept,
        req: LaunchRequest,
        result: LaunchResult,
        approval_status: str,
        preview: dict,
    ) -> None:
        """Build → log (JSON line) → persist the secret-free launch record."""
        record = build_launch_record(
            req,
            result,
            dry_run=self.settings.dry_run,
            approval_status=approval_status,
            backend=self.settings.bankr_backend,
            prompt=preview["prompt"],
            paired_ticker=concept.paired_ticker,
            cli_command=preview["cli_args"] if self.settings.bankr_backend == "cli" else None,
        )
        log_launch_record(record)
        await self.store.save_launch(req, result, record=record)
        log.info(
            "launch %s -> %s pair=%s mode=%s %s",
            req.symbol,
            result.status.value,
            result.pair_status.value,
            record["final_mode"],
            result.token_address,
        )

    async def _attempt_launch(self, concept: Concept) -> LaunchResult | None:
        result = await self.gated_launch(concept)
        if result and result.status in (
            LaunchStatus.CONFIRMED,
            LaunchStatus.SUBMITTED,
            LaunchStatus.SIMULATED,
        ):
            if self.promoter:
                await self.promoter.promote(concept, result)
        return result

    # ---- fees ----------------------------------------------------------------
    async def _fee_sweep(self) -> None:
        treasury = self.settings.treasury
        if not treasury:
            return
        addresses = await self.store.confirmed_token_addresses()
        if not addresses:
            return
        claimable_total = 0.0
        async with FeeReader(self.settings.bankr_api_base) as reader:
            for addr in addresses:
                snap = await reader.claimable_for(addr, treasury)
                snap.beneficiary = treasury
                await self.store.save_fee_snapshot(snap)
                claimable_total += snap.claimable_weth
        log.info(
            "fee sweep: %d tokens, %.6f WETH claimable -> treasury %s",
            len(addresses),
            claimable_total,
            treasury,
        )
        if not self.settings.auto_claim:
            log.info("auto_claim off — monitoring only, not claiming")
            return
        # Only escalate to a claim when there's meaningful value (avoid dust/gas).
        if claimable_total >= self.settings.fee_claim_min_weth:
            await self._maybe_claim(addresses, claimable_total)

    async def _maybe_claim(self, addresses: list[str], total_weth: float) -> None:
        treasury = self.settings.treasury
        summary = (
            f"CLAIM fees: {len(addresses)} tokens, ~{total_weth:.6f} WETH -> {treasury}"
        )
        approval_status = "not_required (dry-run)" if self.settings.dry_run else "auto (approval off)"

        if self.settings.dry_run:
            await self.tg.send(f"🧪 [dry-run] would claim — {summary}")
            await self._record_claim(addresses, total_weth, approval_status, "dry-run", True, "suppressed (dry-run)")
            return

        if self.settings.require_approval:
            approval = Approval(kind=ApprovalKind.CLAIM, ref_id=",".join(addresses)[:200], summary=summary)
            await self.store.save_approval(approval)
            ok = await self.tg.request_approval(approval.id, summary)
            approval.status = "approved" if ok else "rejected"
            approval.decided_at = time.time()
            await self.store.save_approval(approval)
            approval_status = approval.status
            if not ok:
                await self._record_claim(addresses, total_weth, approval_status, "denied", False, "operator denied")
                return

        # Prefer a real wallet claim (broadcasts to treasury) when a hot key is
        # present; otherwise build UNSIGNED txs for the operator to sign.
        if self.settings.bankr_private_key:
            outcome = await self.claimer.claim_wallet_cli()
        else:
            outcome = await self.claimer.build_unsigned(addresses)
        await self._record_claim(
            addresses, total_weth, approval_status, outcome.mode, outcome.ok, outcome.detail
        )
        await self.tg.send(
            f"{'✅' if outcome.ok else '❌'} claim [{outcome.mode}] {outcome.detail}"
        )

    async def _record_claim(
        self,
        addresses: list[str],
        total_weth: float,
        approval_status: str,
        mode: str,
        ok: bool,
        detail: str,
    ) -> None:
        """Build → log (JSON line) → persist a secret-free fee-claim record."""
        record = build_claim_record(
            treasury=self.settings.treasury,
            token_addresses=addresses,
            claimable_weth=total_weth,
            dry_run=self.settings.dry_run,
            approval_status=approval_status,
            mode=mode,
            ok=ok,
            detail=detail,
            at=time.time(),
        )
        log_claim_record(record)
        await self.store.save_claim_record(record)

    # ---- telegram commands ---------------------------------------------------
    def _register_commands(self) -> None:
        self.tg.register("help", self._cmd_help)
        self.tg.register("status", self._cmd_status)
        self.tg.register("launch", self._cmd_launch)
        self.tg.register("claim", self._cmd_claim)
        self.tg.register("pause", self._cmd_pause)
        self.tg.register("resume", self._cmd_resume)

    async def _cmd_help(self, _: str) -> str:
        return (
            "Commands:\n"
            "/status — health, budget, circuit\n"
            "/launch <TICKER> [headline] — queue a manual candidate\n"
            "/claim — sweep + claim fees now\n"
            "/pause — halt the pipeline\n"
            "/resume — resume the pipeline"
        )

    async def _cmd_status(self, _: str) -> str:
        remaining = await self.rate.remaining_today()
        used = await self.store.get_daily_counter(self.rate.counter_name)
        cb = self.breaker.snapshot()
        mode = "dry-run" if self.settings.dry_run else (
            "AUTONOMOUS" if self.settings.autonomous else "approval-gated"
        )
        return (
            f"StockForge status\n"
            f"mode={mode} paused={self._paused} dry_run={self.settings.dry_run} "
            f"approval={self.settings.require_approval}\n"
            f"backend={self.settings.bankr_backend} chain={self.settings.default_chain}\n"
            f"launches today={used} remaining={remaining} (cap {self.rate.effective_daily})\n"
            f"auto_claim={self.settings.auto_claim} treasury={self.settings.treasury or 'unset'}\n"
            f"circuit={cb['state']} fails={cb['consecutive_failures']}\n"
            f"watchlist={','.join(self.settings.watchlist)}"
        )

    async def _cmd_launch(self, arg: str) -> str:
        if not arg:
            return "usage: /launch <TICKER> [headline]"
        ticker, _, headline = arg.partition(" ")
        sig = self.manual.push(ticker, headline)
        return f"queued manual candidate {sig.ticker} (will be scored + gated next tick)"

    async def _cmd_claim(self, _: str) -> str:
        await self._fee_sweep()
        return "fee sweep triggered"

    async def _cmd_pause(self, _: str) -> str:
        self._paused = True
        return "⏸ paused"

    async def _cmd_resume(self, _: str) -> str:
        self._paused = False
        return "▶️ resumed"
