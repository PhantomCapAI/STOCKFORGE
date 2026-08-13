# CLAUDE.md — StockForge operating manual for AI agents

This file orients future Claude Code / Grok Build iterations. Read it before
touching anything. It encodes what is **real**, what is **assumed**, and the
**rules** that keep this system from moving money it shouldn't.

## What this is

An autonomous agent that turns high-attention **stock narratives** into
**Bankr-launched tokens**, promotes them, and **claims trading fees** to fund its
own compute. Async Python, SQLite state, Telegram human-in-the-loop.

## Current state (Phase 1–3 shipped)

- ✅ Clean skeleton, packaging, Docker/Zeabur config.
- ✅ Bankr launcher (CLI + REST backends) with a hard dry-run switch.
- ✅ Fee reader + claimer against the **verified public** Doppler endpoints.
- ✅ Orchestrator loop, SQLite persistence, circuit breaker, rate limiter.
- ✅ Telegram control plane: `/status /launch /claim /pause /resume` + Approve/Reject.
- ✅ Concept forge (deterministic template + optional OpenAI-compatible LLM),
  attention scoring, anti-slop/uniqueness checks.
- ✅ 23 tests, ruff-clean.

**Not yet wired (intentional — ships over polish):**
- Real signal sources (X/Twitter trends, news, on-chain flow). Only a low-score
  watchlist heuristic + a manual `/launch` queue exist today.
- Image generation (the `image_prompt` is produced; no provider is attached).
- Auto-posting to public social (promotion is composed + sent to the operator;
  public posting stays human-gated on purpose).

## Verified vs assumed — DO NOT blur these

**Verified** (docs.bankr.bot, July 2026):
- `POST https://api.bankr.bot/agent/prompt` (`X-API-Key`) launches via NL prompt.
- `bankr --ni launch …` CLI flags: `--name --symbol --chain {base|robinhood}
  --image --tweet --website --fee --fee-type --no-vesting --simulate --yes`.
- Public fee reads + `POST /public/doppler/build-claim`.
- Limits 50/100 per day, 1/min, failures count. 95/5 fee split, 0.7% swap fee.

**Assumed / UNVERIFIED — flag, never fabricate:**
- ❌ `POST /token-launches/deploy` (from the original brief) is **not** a real
  Bankr endpoint. Do not "restore" it. Real path = `/agent/prompt` or CLI.
- ⚠️ **Stock-pairing** (pool quoted in NVDA/GME/TSLA instead of WETH) is **live
  on Bankr** but has **no documented API/CLI parameter**. StockForge:
  - Expresses it via the NL prompt only, and only when `chain=robinhood`:
    `deploy a token called "X" with symbol Y paired with NVDA on robinhood chain`
    (`launcher/pairing.py::stock_pair_phrase`).
  - The **CLI has no pairing flag** — `CLI_SUPPORTS_STOCK_PAIR=False`; do NOT
    invent `--pair`. Only `BANKR_BACKEND=rest` can request a pair.
  - Classifies the outcome honestly from the response
    (`classify_pairing`): `not_requested | requested | accepted | degraded |
    rejected`. `requested` = asked but unverifiable (dry-run / pending); never a
    false "accepted". Degrades safely to a standard pool.
  - TODO for a human: confirm the exact pairing verb + the response label key
    from one real launch, then tighten `find_quote_labels`.
- ⚠️ The Agent API **job-polling path** isn't fully specified publicly. The REST
  backend tries `/agent/job/{id}`, treats 404 as "leave SUBMITTED", and never
  fabricates a token address.

## Human Verification Gate (operator, in order — dry-run stays ON until step 5)

The primary operator commands right now are **`doctor`, `preflight`, `treasury`,
`confirm-pair`**. Run the gate in order on a real machine:

1. `stockforge doctor` **and** `stockforge preflight` — both green
   (`preflight` must reach `✅ READY FOR LIVE`).
2. Do ONE real stock-paired launch on Bankr yourself (Robinhood Chain); confirm
   the pool is quoted in the stock. Only a human can close this.
3. `stockforge confirm-pair <0xtoken> --note "…"` — record the verified pairing.
4. `stockforge treasury` — confirm the token, the `✅pair` mark, and claimed-fee
   tracking look right.
5. Only then consider `STOCKFORGE_DRY_RUN=false` (start budget=1, keep approval on).

## Verify before going live

- `stockforge doctor` — fail-closed readiness check: dry-run flag, Bankr auth +
  reachability, CLI installed/authenticated (`bankr whoami`), Telegram token +
  chat reachability (`getMe`/`getChat`), rate-limiter/budget state. Exits
  non-zero on any ❌.
- `stockforge preflight` — pre-live checklist: "are you ready to flip dry-run
  off?" Reuses the doctor checks but is **not** satisfied in dry-run — it exits
  non-zero (`⛔ NOT READY FOR LIVE`) until every live prerequisite is present, and
  always flags stock-pairing as UNVERIFIED. Use this as the go/no-go gate.
- `stockforge selfcheck [TICKER] [--chain robinhood] [--live-approval]` — runs a
  full **dry-run** pipeline (signal → concept → launch(sim) → fee check →
  approval flow) so a human can verify everything without spending a launch.
  Refuses to run if `dry_run=false`.
- `stockforge launch <TICKER> [--chain]` — the **controlled single-launch** path.
  Respects dry-run (default → SIMULATED, nothing broadcasts) and, when live,
  requires a Telegram approval tap. Every attempt logs the exact prompt and a
  secret-free `launch_record` (see Observability below).

## Observability

Every launch attempt — dry-run included — emits one secret-free structured
`launch_record` JSON line to stdout (`observability.py`) **and** is persisted to
SQLite (`launches.data.record`). Fields: timestamp, name, ticker, requested pair,
final mode (stock-pair / standard), dry-run flag, approval status, and a Bankr
response summary (status/token/job/error). No API keys or private keys ever
appear — the only address included is the public beneficiary/fee recipient.

## Path to First Live Launch (do these in order; dry-run stays ON until step 5)

1. `stockforge doctor` (config sanity) **and** `stockforge preflight` (live
   prerequisites — must reach `✅ READY FOR LIVE`).
2. **Manually** do one real stock-paired launch on Bankr yourself and confirm the
   pool is quoted in the stock. This is the only way to close the `pair_status`
   unknown — the agent cannot verify the pairing contract for you.
3. Fill real `.env` with `STOCKFORGE_DRY_RUN=true`: `BANKR_API_KEY`,
   `BANKR_BENEFICIARY_ADDRESS`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
   dedicated minimally-funded hot wallet.
4. `stockforge selfcheck --live-approval` (buttons must work) then
   `stockforge launch NVDA --chain robinhood` (single dry-run launch); confirm the
   `launch_record` line looks correct.
5. Only then set `STOCKFORGE_DAILY_LAUNCH_BUDGET=1`, flip
   `STOCKFORGE_DRY_RUN=false`, and run ONE `stockforge launch <TICKER>` — it will
   require a Telegram approval tap before anything broadcasts.

## DO NOT turn off dry-run until ALL of these are true

1. `stockforge doctor` exits 0 with **no ❌** in the intended live config.
2. `BANKR_API_KEY` (rest) or `bankr whoami` (cli) works — auth is real.
3. `BANKR_BENEFICIARY_ADDRESS` set (so fees can be read/claimed).
4. Telegram `getMe` + `getChat` both pass (approvals can actually reach a human;
   otherwise every real action is denied, fail-closed).
5. `STOCKFORGE_DAILY_LAUNCH_BUDGET` starts small (1).
6. A dedicated, minimally-funded hot wallet — never a main key.
7. You ran `stockforge selfcheck --live-approval` and the Approve/Reject buttons
   worked in Telegram.
8. For stock-pairing specifically: `BANKR_BACKEND=rest` and `chain=robinhood`;
   confirm one launch reports `pair_status=accepted` before trusting it.

## Continuous fee-faucet operation

`stockforge run` is the continuous engine. Each tick it finds the strongest
narrative, forges a concept, launches (gated), routes creator fees to the
**treasury**, and periodically sweeps + claims fees. Two `.env` switches set how
autonomous it is — both default safe:

- `STOCKFORGE_REQUIRE_APPROVAL` — `true` = Telegram tap per real launch/claim;
  `false` = **autonomous** (no tap). `Settings.autonomous` is true only when
  approval is off AND dry-run is off. Autonomy never bypasses the daily budget,
  the 1/min rate limit, the circuit breaker, or the `/pause` kill switch.
- `STOCKFORGE_AUTO_CLAIM` (default true) — sweeps auto-claim once claimable
  ≥ `STOCKFORGE_FEE_CLAIM_MIN_WETH`. Autonomous on-chain claiming needs
  `BANKR_PRIVATE_KEY`; without it, claims are built UNSIGNED for a human to sign.

**Treasury.** `STOCKFORGE_TREASURY_ADDRESS` (defaults to
`BANKR_BENEFICIARY_ADDRESS`) is the single fee recipient + claim destination
(`Settings.treasury`). Both launch fee routing and claims use it.

**Multi-wallet (`wallets.py`) — ONE honest operation, several wallets.**
`STOCKFORGE_WALLETS` (JSON) defines a pool for legitimate ops reasons only: key
segregation/opsec, treasury splitting, SPOF reduction, and respecting each
wallet's own Bankr cap. Launches distribute least-recently-used across wallets;
each wallet independently enforces Bankr's 50/100 + 1/min (per-wallet limiter),
and `STOCKFORGE_DAILY_LAUNCH_BUDGET` is the GLOBAL hard ceiling across all wallets
(`self.rate`, counter `launch_all`, `cap_to_bankr=False`). Every launch is
attributed (`LaunchRequest.wallet_id` → record + `/status` + `/treasury`). Fees
route to each wallet's `fee_recipient` (defaults to the treasury, so they
consolidate). Empty pool = one `main` wallet (backward compatible). **This is NOT
a disguise:** attribution is open, wallets are disclosed as one operation, and
multiplying past Bankr's per-account caps via many accounts is sybil behavior we
do not build. Per-wallet REST launching uses each wallet's `api_key`.

**Promotion (`promo/promoter.py`) — operator-gated.** Each launch builds a
`PromoKit` (tweet, one-liner, launch link) and notifies the operator via the
`OperatorNotifyPublisher`. Public posting stays human-gated; a `Publisher` seam
lets a real X/content agent plug in later. The "not affiliated" disclaimer is
always included. `STOCKFORGE_PROMO_ENABLED` toggles it.

**Fee sweeps are wallet-pool-aware.** `_fee_sweep` reads claimable for every
launched token **against the address it actually routes to** (each wallet's
`fee_recipient`), groups by recipient, and claims per group: a wallet with its
own `private_key` claims via the CLI with that key; otherwise unsigned txs are
built addressed to that recipient. Each claim is attributed (`wallet_id`) in the
`claim_record`. `STOCKFORGE_FEE_CLAIM_MIN_WETH` gates per-group dust.

**Stock-pair confirmation.** Pairing is UNVERIFIED until a human checks a real
launch. After confirming the pool is quoted in the stock on Bankr, the operator
marks it: `stockforge confirm-pair <token>` or `/confirmpair <token>` (persisted
in `pair_confirmations`). `stockforge treasury` lists confirmed vs pending-
verification stock-paired tokens.

**Observability.** Every launch → secret-free `launch_record`; every claim →
secret-free `claim_record` (both JSON to stdout + persisted: `launches`/`claims`
tables). `stockforge treasury` / `/treasury` show claimed WETH, per-wallet
attribution, top producing tokens, pair-verification status, and recent claims.
Per-tick heartbeat logs budget remaining, circuit state, and mode.

**Fees → compute (self-funding loop).** `FORGE_LLM_PROVIDER=bankr` points the
concept forge at the **Bankr LLM Gateway** (`llm.bankr.bot`, verified,
OpenAI-compatible, `X-API-Key`), so the agent's own trading fees pay for its
compute — the official Bankr loop: wallet → launch → fees → claim → pay compute →
keep running. LLM credits are USD, separate from the trading wallet; top up on a
funded account (`bankr llm credits add` / `... auto`). `compute.py` centralizes
this; it does **not** auto-buy credits (deliberate human step — real spend).
`stockforge treasury` / `/treasury` show extracted fees + funding status.

**Intended expansion path** (documented in OVERVIEW.md — do not over-build):
1. Continuous stock-paired launches (current single engine).
2. Fee consolidation into one treasury (current).
3. Fees → compute via the Bankr LLM Gateway (foundation present; auto-topup stays
   a human-enabled step).
4. Later: x402 paid endpoints as a second revenue stream (seam only, not built).

**Boundary — no sybil / limit evasion.** Bankr enforces per-account daily caps
(50/100), 1/min, an anti-sybil wallet-age gate, and spam detection that restricts
accounts. The engine runs as ONE account within those limits. `LaunchRequest.fee_recipient`
already allows routing fees to different treasuries *within one account* (benign
organization). Do NOT build multi-account/multi-wallet machinery whose purpose is
to multiply past those caps — that's ToS-violating sybil behavior and gets
accounts banned. Keep the anti-slop checks and the "not affiliated with <TICKER>"
disclaimer — they are anti-spam / anti-deception rails, not optional.

## Dual-mode launching (stock-paired + standard, both first-class)

`STOCKFORGE_LAUNCH_MODE` = `auto` (default) | `stock_paired` | `standard`.
`launcher/pairing.py::resolve_pair_with(mode, ticker, chain, is_stock)` decides
whether a launch requests a pair — only when mode allows AND `chain=robinhood`
AND the ticker is a watchlist stock; otherwise a first-class STANDARD launch. No
Bankr pairing param is fabricated. In `auto`, a stock-paired launch that FAILS is
retried once as standard (`gated_launch` → `force_standard=True`). Operators force
per-launch with `stockforge launch/preview --mode {auto|stock|standard}`. Records
carry `launch_mode` + `pair_status` + `final_mode`; `treasury` shows the
stock-paired/standard split. The system is NOT hardcoded stock-only — standard
keeps it alive when pairing is weak/unavailable.

## Hard rules (safety > features)

1. **Dry-run is the default.** `STOCKFORGE_DRY_RUN=true` means nothing broadcasts.
   The REST backend refuses to broadcast in dry-run; the CLI backend uses
   `--simulate`. Never remove this guard.
2. **Secrets only in `.env`.** No keys in code, logs, commits, or PRs. Use
   `Settings.redacted()` for any config you log. `.env` is gitignored.
3. **Human-in-the-loop is the default, autonomy is opt-in.** Real launches and
   claims require Telegram approval unless `STOCKFORGE_REQUIRE_APPROVAL=false` is
   deliberately set. No Telegram configured + approval required ⇒ **fail-closed**
   (deny). The `/pause` kill switch must always work — never remove it.
4. **Respect Bankr limits.** All launch attempts go through `LaunchRateLimiter`
   (counts failures, enforces 1/min + daily cap). Don't bypass it.
5. **Circuit breaker.** 3 consecutive Bankr failures opens it for 5 min. Don't
   catch-and-ignore breaker errors in the launch path.
6. **Clean on-chain footprint.** Anti-slop rejects derivative/duplicate concepts.
   Always include the "not affiliated with <TICKER>" disclaimer. Don't auto-post
   to public social without a human.
7. **Never invent an API contract.** If a Bankr capability isn't in the verified
   list above, read the current docs first (don't guess), mark it UNVERIFIED, and
   make it env-configurable.

## Where to change things

| Want to… | Edit |
|---|---|
| Add a real attention source | new class in `signal/sources.py` (impl `poll()`) |
| Improve scoring | `signal/scorer.py` (keep `score(signal)->float` 0–100) |
| Change concept style | `prompts/concept_generation.md` + `forge/concept.py` |
| Tune anti-slop | `forge/antislop.py` |
| Wire a structured Bankr deploy route | `launcher/bankr_rest.py` + `launcher/base.py` |
| Add a Telegram command | `orchestrator/loop.py::_register_commands` |
| Change launch/faucet policy | `.env` (`STOCKFORGE_*`) — no code change |
| Route fees / set treasury | `.env` `STOCKFORGE_TREASURY_ADDRESS` (defaults to beneficiary) |
| Add operation wallets | `.env` `STOCKFORGE_WALLETS` (JSON) — honest pool, `wallets.py` |
| Plug in real X posting | new `Publisher` in `promo/promoter.py` (keep public posting human-gated) |
| Go autonomous (no taps) | `.env` `STOCKFORGE_REQUIRE_APPROVAL=false` (still gated by budget/rate/dry-run) |

## Run / test

```bash
pip install -r requirements-dev.txt && pip install -e .
stockforge doctor && stockforge preview NVDA     # safe
pytest -q && ruff check src tests
stockforge run                                    # dry-run loop
```

## Attribution / model note

Do not embed model identifiers or internal session URLs in commits, code, or
PRs. Keep that to chat only.
