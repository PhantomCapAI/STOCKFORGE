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
from ..forge.image import ImageForge
from ..launcher import BankrLauncher
from ..launcher.pairing import resolve_pair_with
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
    ElonTweetSource,
    ManualSource,
    NewsRssSource,
    TweetInbox,
    WatchlistHeuristicSource,
    XApiTweetProvider,
)
from ..wallets import Wallet, WalletPool
from .telegram import TelegramControl

log = get_logger("orchestrator")


class Orchestrator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings.db_path)
        self.scorer = AttentionScorer()
        self.forge = ConceptForge(settings)
        self.image_forge = ImageForge(settings)
        self.breaker = CircuitBreaker("bankr", failure_threshold=3, reset_timeout=300)
        self.launcher = BankrLauncher(settings, breaker=self.breaker)
        self.claimer = FeeClaimer(settings)
        self.promoter: Promoter | None = None
        # One honest operation, one or more wallets. Each wallet respects Bankr's
        # per-wallet cap; `self.rate` is the GLOBAL ceiling across all wallets.
        self.pool = WalletPool.from_settings(settings)
        self.rate = LaunchRateLimiter(
            self.store,
            daily_budget=settings.daily_launch_budget,
            counter_name="launch_all",
            cap_to_bankr=False,
        )
        self.manual = ManualSource()
        # Elon-tweet inbox: also used by the /elon command to inject a test tweet
        # when running without the paid X API / Grok provider.
        self.tweet_inbox = TweetInbox()
        # Real attention first (news, elon), then the baseline heuristic + manual
        # queue. The heuristic keeps the pipeline exercised even when nothing hits.
        self.sources: list = []
        if settings.news_source_enabled:
            self.sources.append(
                NewsRssSource(
                    settings.watchlist,
                    freshness_hours=settings.news_freshness_hours,
                )
            )
        if settings.elon_source_enabled:
            self.sources.append(self._build_elon_source())
        self.sources += [WatchlistHeuristicSource(settings.watchlist), self.manual]
        self.tg = TelegramControl(settings.telegram_bot_token, settings.telegram_chat_id)
        self.promoter = Promoter(
            notifier=self.tg.send,
            link_base=settings.promo_link_base,
            enabled=settings.promo_enabled,
        )
        self._paused = False
        self._stop = asyncio.Event()
        self._tick = 0

    def _build_elon_source(self) -> ElonTweetSource:
        """Pick the tweet provider by config: X API (paid), Grok live-search
        (xAI key), or the manual/webhook inbox. Falls back to the inbox so the
        source always runs."""
        s = self.settings
        provider = self.tweet_inbox  # default: manual / webhook inbox
        if s.elon_provider == "x_api" and s.x_bearer_token:
            provider = XApiTweetProvider(s.x_bearer_token, user_id=s.elon_user_id)
        elif s.elon_provider == "grok" and s.xai_api_key:
            try:
                from ..signal.elon import GrokTweetProvider

                provider = GrokTweetProvider(
                    s.xai_api_key,
                    user_id=s.elon_user_id,
                    base_url=s.xai_base_url,
                    model=s.xai_model,
                )
            except ImportError:
                log.warning("grok provider unavailable — falling back to inbox")
        return ElonTweetSource(provider, min_engagement=float(s.elon_min_engagement))

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
        await self.image_forge.aclose()
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
        log.info(
            "wallets: %d in pool %s | per-wallet cap %d/day | global budget %d/day",
            len(self.pool.wallets),
            [w["id"] for w in self.pool.redacted()],
            self.settings.per_wallet_daily_cap,
            self.settings.daily_launch_budget,
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
            "tick %d | global budget %d/%d left | wallets=%d | circuit=%s | mode=%s",
            self._tick,
            remaining,
            self.rate.effective_daily,
            len(self.pool.wallets),
            self.breaker.state.value,
            "dry-run" if self.settings.dry_run
            else ("autonomous" if self.settings.autonomous else "approval"),
        )

    async def _select_wallet(self) -> tuple[Wallet, LaunchRateLimiter] | None:
        return await self.pool.select(
            self.store,
            global_budget=self.settings.daily_launch_budget,
            per_wallet_cap=self.settings.per_wallet_daily_cap,
        )

    async def _pipeline_tick(self) -> None:
        # Fast pre-gate: if no wallet can launch right now (global budget spent, or
        # every wallet in 1-min cooldown / at its cap), don't spend network on
        # signal polling + forging this tick.
        if (await self._select_wallet()) is None:
            log.debug("tick skipped: no eligible wallet (budget/cooldown)")
            return

        candidates = await self._eligible_candidates()
        if not candidates:
            return

        # Try candidates best-first until one forges a clean concept AND the launch
        # path accepts it. Per-wallet 1/min + the global budget still bound how many
        # actually launch — this just avoids wasting a tick on an anti-slop reject.
        recent = await self.store.recent_concept_slugs()
        for signal in candidates:
            log.info("candidate %s score=%.0f", signal.ticker, signal.attention_score)
            await self.store.save_signal(signal)
            concept = await self.forge.forge(signal, recent_slugs=recent)
            if concept is None:
                log.info("no clean concept for %s; trying next", signal.ticker)
                continue
            await self._enrich_metadata(concept, signal)
            await self.store.save_concept(concept)
            result = await self._attempt_launch(concept)
            # Stop once a launch was attempted or no wallet has capacity left.
            if result is not None or (await self._select_wallet()) is None:
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

    # ---- metadata enrichment -------------------------------------------------
    async def _enrich_metadata(self, concept: Concept, signal: Signal) -> None:
        """Attach the source tweet and — only for strong/meme-worthy launches —
        a generated image. Image gen fires when the signal clears image_min_score
        (so it's reserved for viral picks, e.g. an Elon meme), not every token."""
        concept.source_tweet_url = str(signal.meta.get("tweet_url", "") or "")
        if (
            self.image_forge.enabled
            and not concept.image_url
            and concept.image_prompt
            and signal.attention_score >= self.settings.image_min_score
        ):
            log.info(
                "generating image for $%s (score %.0f >= %d)",
                concept.symbol,
                signal.attention_score,
                self.settings.image_min_score,
            )
            concept.image_url = await self.image_forge.generate(concept.image_prompt)

    # ---- launch --------------------------------------------------------------
    def _is_stock(self, ticker: str) -> bool:
        """A ticker is stock-pairable if it's one of our recognized stocks.
        Non-stock / generic narratives route to a standard launch."""
        return ticker.upper() in {t.upper() for t in self.settings.watchlist}

    def _build_request(
        self, concept: Concept, wallet: Wallet, mode: str | None = None, force_standard: bool = False
    ) -> LaunchRequest:
        effective_mode = (mode or self.settings.launch_mode).lower()
        if force_standard:
            pair_with, note = "", "standard (degraded from stock-pair)"
        else:
            pair_with, note = resolve_pair_with(
                effective_mode,
                concept.paired_ticker,
                self.settings.default_chain,
                self._is_stock(concept.paired_ticker),
            )
        log.debug("launch mode=%s -> %s", effective_mode, note)
        return LaunchRequest(
            concept_id=concept.id,
            name=concept.name,
            symbol=concept.symbol,
            launch_mode=effective_mode,
            chain=self.settings.default_chain,
            wallet_id=wallet.id,
            image_url=concept.image_url,
            tweet_url=concept.source_tweet_url,
            website=self.settings.default_website,
            # Route creator fees to this wallet's recipient (defaults to the main
            # treasury, so fees consolidate regardless of which wallet launched).
            fee_recipient=wallet.fee_recipient,
            fee_recipient_type="address" if wallet.fee_recipient else "",
            disable_vesting=self.settings.disable_vesting,
            # Dual-mode: pair_with is set only when the mode + chain + ticker allow
            # it (resolve_pair_with). Empty = a first-class STANDARD launch.
            pair_with=pair_with,
        )

    async def gated_launch(
        self, concept: Concept, mode: str | None = None, force_standard: bool = False
    ) -> LaunchResult | None:
        """The single controlled launch path: pick wallet → per-wallet + global
        rate-limit → approval → dry-run → Bankr → structured record. Returns None
        if gated or rejected. Used by the loop and the one-shot CLI `launch`.

        `mode` (auto/stock_paired/standard) overrides the configured launch mode
        for this call. In `auto`, a stock-paired launch that FAILS is retried once
        as a standard launch (safe degradation)."""
        picked = await self._select_wallet()
        if picked is None:
            log.info("launch gated: no eligible wallet (global budget spent or all in cooldown/capped)")
            return None
        wallet, wallet_rl = picked

        req = self._build_request(concept, wallet, mode=mode, force_standard=force_standard)
        preview = self.launcher.preview(req)
        launch_kind = "STOCK-PAIRED" if req.pair_with else "STANDARD"
        summary = (
            f"LAUNCH ${req.symbol} — {req.name}\n"
            f"wallet={wallet.id} mode={req.launch_mode} kind={launch_kind} "
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
                denied = LaunchResult(
                    request_id=req.id,
                    wallet_id=wallet.id,
                    status=LaunchStatus.REJECTED,
                    error="operator denied",
                )
                await self._record(concept, req, denied, approval_status, preview)
                return None
        else:
            await self.tg.send(f"▶️ Auto-launch (dry_run={self.settings.dry_run})\n{summary}")

        # Count the attempt against the wallet AND the global ceiling BEFORE
        # calling (Bankr counts failures too).
        await self.pool.record_launch(self.store, wallet, wallet_rl)
        result = await self.launcher.launch(req, api_key=wallet.api_key or None)
        await self._record(concept, req, result, approval_status, preview)

        pair_line = ""
        if result.pair_requested:
            pair_line = f"\npair {result.pair_requested.upper()}: {result.pair_status.value}"
        await self.tg.send(
            f"{'✅' if result.status not in (LaunchStatus.FAILED,) else '❌'} "
            f"[{wallet.id}] ${req.symbol} {result.status.value} "
            f"{result.token_address or result.error}{pair_line}"
        )

        # Safe degradation (auto mode only): a stock-paired launch that FAILED gets
        # ONE standard retry, so the system doesn't die when pairing is unavailable.
        effective_mode = (mode or self.settings.launch_mode).lower()
        if (
            effective_mode == "auto"
            and req.pair_with
            and result.status is LaunchStatus.FAILED
            and not force_standard
        ):
            picked2 = await self._select_wallet()
            if picked2 is not None:
                w2, rl2 = picked2
                log.warning("stock-pair launch failed for %s — degrading to STANDARD retry", req.symbol)
                await self.tg.send(f"↘️ [{w2.id}] ${req.symbol} stock-pair failed — retrying STANDARD")
                std_req = self._build_request(concept, w2, mode="auto", force_standard=True)
                await self.pool.record_launch(self.store, w2, rl2)
                std_result = await self.launcher.launch(std_req, api_key=w2.api_key or None)
                await self._record(concept, std_req, std_result, approval_status, self.launcher.preview(std_req))
                await self.tg.send(
                    f"{'✅' if std_result.status not in (LaunchStatus.FAILED,) else '❌'} "
                    f"[{w2.id}] ${std_req.symbol} STANDARD {std_result.status.value} "
                    f"{std_result.token_address or std_result.error}"
                )
                return std_result
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
    def _wallet_for_recipient(self, recipient: str) -> Wallet | None:
        return next((w for w in self.pool.wallets if w.fee_recipient == recipient), None)

    async def _fee_sweep(self) -> None:
        """Read claimable fees for every launched token AGAINST THE ADDRESS IT
        ROUTES TO (each wallet's fee_recipient), grouped per recipient. Multi-
        wallet aware; falls back to the treasury for tokens with no recorded
        recipient."""
        treasury = self.settings.treasury
        token_map = await self.store.token_recipients()
        if not token_map:
            return

        # recipient -> {"tokens": [...], "claimable": float}
        groups: dict[str, dict] = {}
        total = 0.0
        async with FeeReader(self.settings.bankr_api_base) as reader:
            for token, meta in token_map.items():
                recipient = meta.get("recipient") or treasury
                if not recipient:
                    continue
                snap = await reader.claimable_for(token, recipient)
                snap.beneficiary = recipient
                await self.store.save_fee_snapshot(snap)
                g = groups.setdefault(recipient, {"tokens": [], "claimable": 0.0})
                g["tokens"].append(token)
                g["claimable"] += snap.claimable_weth
                total += snap.claimable_weth

        per_recipient = ", ".join(
            f"{self._recipient_label(r)}={g['claimable']:.6f}" for r, g in groups.items()
        )
        log.info(
            "fee sweep: %d tokens across %d recipient(s), %.6f WETH claimable [%s]",
            len(token_map),
            len(groups),
            total,
            per_recipient or "none",
        )
        if not self.settings.auto_claim:
            log.info("auto_claim off — monitoring only, not claiming")
            return
        # Claim per recipient group whose claimable clears the dust threshold.
        for recipient, g in groups.items():
            if g["claimable"] >= self.settings.fee_claim_min_weth:
                await self._maybe_claim(recipient, g["tokens"], g["claimable"])

    def _recipient_label(self, recipient: str) -> str:
        w = self._wallet_for_recipient(recipient)
        return f"{w.id}" if w else (recipient[:10] + "…" if recipient else "?")

    async def _maybe_claim(self, recipient: str, addresses: list[str], total_weth: float) -> None:
        wallet = self._wallet_for_recipient(recipient)
        wlabel = wallet.id if wallet else self._recipient_label(recipient)
        summary = (
            f"CLAIM fees [{wlabel}]: {len(addresses)} tokens, ~{total_weth:.6f} WETH -> {recipient}"
        )
        approval_status = "not_required (dry-run)" if self.settings.dry_run else "auto (approval off)"

        if self.settings.dry_run:
            await self.tg.send(f"🧪 [dry-run] would claim — {summary}")
            await self._record_claim(recipient, wlabel, addresses, total_weth, approval_status,
                                     "dry-run", True, "suppressed (dry-run)")
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
                await self._record_claim(recipient, wlabel, addresses, total_weth, approval_status,
                                         "denied", False, "operator denied")
                return

        # Prefer a real wallet claim using THIS wallet's hot key; otherwise build
        # UNSIGNED txs addressed to this recipient for the operator to sign.
        wallet_key = wallet.private_key if wallet else ""
        if wallet_key or self.settings.bankr_private_key:
            outcome = await self.claimer.claim_wallet_cli(private_key=wallet_key or None)
        else:
            outcome = await self.claimer.build_unsigned(addresses, beneficiary=recipient)
        await self._record_claim(
            recipient, wlabel, addresses, total_weth, approval_status,
            outcome.mode, outcome.ok, outcome.detail,
        )
        await self.tg.send(
            f"{'✅' if outcome.ok else '❌'} claim [{wlabel}/{outcome.mode}] {outcome.detail}"
        )

    async def _record_claim(
        self,
        recipient: str,
        wallet_id: str,
        addresses: list[str],
        total_weth: float,
        approval_status: str,
        mode: str,
        ok: bool,
        detail: str,
    ) -> None:
        """Build → log (JSON line) → persist a secret-free fee-claim record."""
        record = build_claim_record(
            treasury=recipient,
            token_addresses=addresses,
            claimable_weth=total_weth,
            dry_run=self.settings.dry_run,
            approval_status=approval_status,
            mode=mode,
            ok=ok,
            detail=detail,
            at=time.time(),
            wallet_id=wallet_id,
        )
        log_claim_record(record)
        await self.store.save_claim_record(record)

    # ---- telegram commands ---------------------------------------------------
    def _register_commands(self) -> None:
        self.tg.register("help", self._cmd_help)
        self.tg.register("status", self._cmd_status)
        self.tg.register("launch", self._cmd_launch)
        self.tg.register("elon", self._cmd_elon)
        self.tg.register("claim", self._cmd_claim)
        self.tg.register("treasury", self._cmd_treasury)
        self.tg.register("promo", self._cmd_promo)
        self.tg.register("confirmpair", self._cmd_confirmpair)
        self.tg.register("pause", self._cmd_pause)
        self.tg.register("resume", self._cmd_resume)

    async def _cmd_help(self, _: str) -> str:
        return (
            "Commands:\n"
            "/status — health, budget, circuit\n"
            "/launch <TICKER> [headline] — queue a manual candidate\n"
            "/elon <tweet text> — inject a test Elon tweet\n"
            "/claim — sweep + claim fees now\n"
            "/treasury — extracted fees + compute funding\n"
            "/promo <TICKER> — full launch copy package (draft, not posted)\n"
            "/confirmpair <0xtoken> [note] — mark a stock-pair verified\n"
            "/pause — halt the pipeline\n"
            "/resume — resume the pipeline"
        )

    async def _cmd_confirmpair(self, arg: str) -> str:
        if not arg:
            return "usage: /confirmpair <0xtoken> [note]"
        token, _, note = arg.partition(" ")
        token = token.strip()
        tmap = await self.store.token_recipients()
        ticker = tmap.get(token, {}).get("ticker", "")
        await self.store.confirm_pair(token, ticker, note.strip())
        return f"✅ stock-pair confirmed for {token[:12]}… ({ticker or 'unknown ticker'})"

    async def _cmd_status(self, _: str) -> str:
        remaining = await self.rate.remaining_today()
        used = await self.store.get_daily_counter(self.rate.counter_name)
        cb = self.breaker.snapshot()
        mode = "dry-run" if self.settings.dry_run else (
            "AUTONOMOUS" if self.settings.autonomous else "approval-gated"
        )
        # Per-wallet usage today (each wallet respects Bankr's per-wallet cap).
        wallet_lines = []
        for w in self.pool.wallets:
            wused = await self.store.get_daily_counter(w.counter())
            wallet_lines.append(f"{w.id}:{wused}/{self.settings.per_wallet_daily_cap}")
        return (
            f"StockForge status\n"
            f"mode={mode} paused={self._paused} dry_run={self.settings.dry_run} "
            f"approval={self.settings.require_approval}\n"
            f"backend={self.settings.bankr_backend} chain={self.settings.default_chain}\n"
            f"global launches today={used} remaining={remaining} (budget {self.rate.effective_daily})\n"
            f"wallets({len(self.pool.wallets)}): {', '.join(wallet_lines)}\n"
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

    async def _cmd_elon(self, arg: str) -> str:
        if not arg:
            return "usage: /elon <tweet text> — inject a test Elon tweet into the pipeline"
        tw = self.tweet_inbox.push(arg, like_count=50_000)
        return (
            f"queued elon tweet {tw.id} — evaluated against the mark next tick "
            "(needs STOCKFORGE_ELON_SOURCE=true)"
        )

    async def _cmd_claim(self, _: str) -> str:
        await self._fee_sweep()
        return "fee sweep triggered"

    async def _cmd_promo(self, arg: str) -> str:
        if not arg:
            return "usage: /promo <TICKER>"
        ticker = arg.split()[0].upper()
        sig = self.scorer.enrich(
            Signal(ticker=ticker, headline=f"{ticker} promo", sources=["operator"], meta={"magnitude": 20})
        )
        concept = await self.forge.forge(sig, recent_slugs=[])
        if concept is None:
            return f"no clean concept for {ticker} right now"
        result = LaunchResult(request_id="preview", status=LaunchStatus.SIMULATED)
        kit = self.promoter.build_kit(concept, result) if self.promoter else None
        if kit is None:
            return "promo disabled"
        return kit.render_full() + "\n\n(Draft only — review + post manually.)"

    async def _cmd_treasury(self, _: str) -> str:
        s = await self.store.claim_summary()
        by_wallet = await self.store.launch_counts_by_wallet()
        recent = await self.store.recent_claims(3)
        confirmed = await self.store.pair_confirmed_map()
        lines = [
            f"💰 Treasury {self.settings.treasury or 'unset'}",
            f"claimed: {s['weth_claimed_recorded']:.6f} WETH "
            f"({s['claim_successes']}/{s['claim_attempts']} claims ok)",
            f"launches by wallet: {', '.join(f'{k}:{v}' for k, v in by_wallet.items()) or 'none'}",
            f"stock-pairs confirmed: {sum(1 for v in confirmed.values() if v['confirmed'])}",
            f"auto_claim={self.settings.auto_claim} min={self.settings.fee_claim_min_weth}",
        ]
        if recent:
            lines.append("recent claims:")
            for r in recent:
                lines.append(
                    f"  {r.get('timestamp', '')[:16]} [{r.get('wallet_id', '?')}/{r.get('mode', '?')}] "
                    f"{r.get('claimable_weth', 0):.6f} WETH ok={r.get('ok')}"
                )
        if self.settings.treasury:
            try:
                async with FeeReader(self.settings.bankr_api_base) as reader:
                    totals = await reader.creator_totals(self.settings.treasury)
                if totals:
                    lines.append(
                        f"Bankr: lifetime {totals.get('lifetime_weth', 0)} / "
                        f"claimable {totals.get('claimable_weth', 0)} WETH "
                        f"({totals.get('token_count', 0)} tokens)"
                    )
            except Exception:  # noqa: BLE001
                pass
        return "\n".join(lines)

    async def _cmd_pause(self, _: str) -> str:
        self._paused = True
        return "⏸ paused"

    async def _cmd_resume(self, _: str) -> str:
        self._paused = False
        return "▶️ resumed"
