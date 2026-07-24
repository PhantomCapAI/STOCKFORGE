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
    LaunchStatus,
    Signal,
)
from ..promo import Promoter
from ..ratelimit import LaunchRateLimiter
from ..signal import AttentionScorer, ManualSource, WatchlistHeuristicSource
from .telegram import TelegramControl

log = get_logger("orchestrator")

FEE_SWEEP_EVERY = 6  # ticks between fee sweeps


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
        self.sources = [WatchlistHeuristicSource(settings.watchlist), self.manual]
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
        if self.settings.dry_run:
            log.warning("DRY-RUN active — no on-chain launches or claims will broadcast")
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

    # ---- main loop -----------------------------------------------------------
    async def _main_loop(self) -> None:
        while not self._stop.is_set():
            self._tick += 1
            try:
                if not self._paused:
                    await self._pipeline_tick()
                    if self._tick % FEE_SWEEP_EVERY == 0:
                        await self._fee_sweep()
            except CircuitOpenError as e:
                log.warning("skipping tick: %s", e)
            except Exception:  # noqa: BLE001
                log.exception("tick failed (continuing)")
            await asyncio.wait(
                [asyncio.create_task(self._stop.wait())],
                timeout=self.settings.tick_seconds,
            )

    async def _pipeline_tick(self) -> None:
        signal = await self._best_candidate()
        if signal is None:
            return
        log.info("candidate %s score=%.0f", signal.ticker, signal.attention_score)
        await self.store.save_signal(signal)

        recent = await self.store.recent_concept_slugs()
        concept = await self.forge.forge(signal, recent_slugs=recent)
        if concept is None:
            log.info("no clean concept for %s this tick", signal.ticker)
            return
        await self.store.save_concept(concept)

        await self._attempt_launch(concept)

    async def _best_candidate(self) -> Signal | None:
        collected: list[Signal] = []
        for src in self.sources:
            try:
                for sig in await src.poll():
                    collected.append(self.scorer.enrich(sig))
            except Exception:  # noqa: BLE001
                log.exception("source %s failed", getattr(src, "name", "?"))
        eligible = [s for s in collected if s.attention_score >= self.settings.min_attention_score]
        if not eligible:
            return None
        eligible.sort(key=lambda s: s.attention_score, reverse=True)
        return eligible[0]

    # ---- launch --------------------------------------------------------------
    async def _attempt_launch(self, concept: Concept) -> None:
        decision = await self.rate.check()
        if not decision.allowed:
            log.info("launch gated: %s", decision.reason)
            return

        req = LaunchRequest(
            concept_id=concept.id,
            name=concept.name,
            symbol=concept.symbol,
            chain=self.settings.default_chain,
            image_url=concept.image_url,
            fee_recipient=self.settings.bankr_beneficiary_address,
            fee_recipient_type="address" if self.settings.bankr_beneficiary_address else "",
            disable_vesting=self.settings.disable_vesting,
            # Stock-pairing intent only meaningful on Robinhood Chain (UNVERIFIED
            # capability — passed through as a hint, see launcher/base.py).
            pair_with=concept.paired_ticker if self.settings.default_chain == "robinhood" else "",
        )

        preview = self.launcher.preview(req)
        summary = (
            f"LAUNCH ${req.symbol} — {req.name}\n"
            f"chain={req.chain} pair={preview['pair_with']} dry_run={self.settings.dry_run}\n"
            f"thesis: {concept.thesis[:160]}\n"
            f"prompt: {preview['prompt']}"
        )

        if self.settings.require_approval and not self.settings.dry_run:
            approval = Approval(kind=ApprovalKind.LAUNCH, ref_id=req.id, summary=summary)
            await self.store.save_approval(approval)
            ok = await self.tg.request_approval(approval.id, summary)
            approval.status = "approved" if ok else "rejected"
            approval.decided_at = time.time()
            await self.store.save_approval(approval)
            if not ok:
                log.info("launch %s rejected by operator", req.symbol)
                return
        else:
            await self.tg.send(f"▶️ Auto-launch (dry_run={self.settings.dry_run})\n{summary}")

        # Count the attempt BEFORE calling (Bankr counts failures too).
        await self.rate.record()
        result = await self.launcher.launch(req)
        await self.store.save_launch(req, result)
        log.info("launch %s -> %s %s", req.symbol, result.status.value, result.token_address)
        await self.tg.send(
            f"{'✅' if result.status not in (LaunchStatus.FAILED,) else '❌'} "
            f"${req.symbol} {result.status.value} {result.token_address or result.error}"
        )
        if result.status in (LaunchStatus.CONFIRMED, LaunchStatus.SUBMITTED, LaunchStatus.SIMULATED):
            if self.promoter:
                await self.promoter.promote(concept, result)

    # ---- fees ----------------------------------------------------------------
    async def _fee_sweep(self) -> None:
        beneficiary = self.settings.bankr_beneficiary_address
        if not beneficiary:
            return
        addresses = await self.store.confirmed_token_addresses()
        if not addresses:
            return
        claimable_total = 0.0
        async with FeeReader(self.settings.bankr_api_base) as reader:
            for addr in addresses:
                snap = await reader.claimable_for(addr, beneficiary)
                snap.beneficiary = beneficiary
                await self.store.save_fee_snapshot(snap)
                claimable_total += snap.claimable_weth
        log.info("fee sweep: %d tokens, %.6f WETH claimable", len(addresses), claimable_total)
        # Only escalate to a claim when there's meaningful value.
        if claimable_total >= 0.001:
            await self._maybe_claim(addresses, claimable_total)

    async def _maybe_claim(self, addresses: list[str], total_weth: float) -> None:
        summary = f"CLAIM fees: {len(addresses)} tokens, ~{total_weth:.6f} WETH claimable"
        if self.settings.dry_run:
            await self.tg.send(f"🧪 [dry-run] would claim — {summary}")
            return
        if self.settings.require_approval:
            approval = Approval(kind=ApprovalKind.CLAIM, ref_id=",".join(addresses)[:200], summary=summary)
            await self.store.save_approval(approval)
            ok = await self.tg.request_approval(approval.id, summary)
            approval.status = "approved" if ok else "rejected"
            approval.decided_at = time.time()
            await self.store.save_approval(approval)
            if not ok:
                return
        if self.settings.bankr_private_key:
            outcome = await self.claimer.claim_wallet_cli()
        else:
            outcome = await self.claimer.build_unsigned(addresses)
        await self.tg.send(
            f"{'✅' if outcome.ok else '❌'} claim [{outcome.mode}] {outcome.detail}"
        )

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
        return (
            f"StockForge status\n"
            f"paused={self._paused} dry_run={self.settings.dry_run} "
            f"approval={self.settings.require_approval}\n"
            f"backend={self.settings.bankr_backend} chain={self.settings.default_chain}\n"
            f"launches today={used} remaining={remaining} (cap {self.rate.effective_daily})\n"
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
