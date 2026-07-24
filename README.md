# PHANTOM STOCKFORGE

Autonomous **stock-narrative token launcher + fee-extraction agent** on
[Bankr](https://bankr.bot). It watches high-attention stock narratives
(NVDA, GME, TSLA, HOOD, SPY, …), forges original token concepts, launches them on
**Robinhood Chain** or **Base** via Bankr, promotes them, claims trading fees, and
recycles those fees into compute — running non-stop with rate-limit respect,
circuit breakers, and human approvals over Telegram.

> **Safety first.** Ships **dry-run by default** (`STOCKFORGE_DRY_RUN=true`).
> Nothing hits the chain until you deliberately turn that off. Real launches and
> claims require a human tap in Telegram (fail-closed). Secrets live only in
> `.env`.

---

## The loop

```
signal ─▶ score ─▶ forge concept ─▶ anti-slop ─▶ rate-limit + budget gate
      ─▶ human approval ─▶ Bankr launch ─▶ persist ─▶ promote
                                                │
              fee sweep ◀── (every N ticks) ────┘
                 └▶ claim (approved) ─▶ fees ─▶ fund compute ─▶ repeat
```

## Quickstart

```bash
cp .env.example .env          # fill in Telegram; Bankr keys optional for dry-run
pip install -r requirements.txt && pip install -e .

stockforge doctor             # readiness check (safe)
stockforge preview NVDA       # forge + preview a launch — nothing is sent
stockforge run                # start the autonomous loop (DRY-RUN by default)
```

### Operator commands (the four you use most right now)

During the verification phase these are the primary commands:

| Command | What it does |
|---|---|
| `stockforge doctor` | fail-closed readiness check — auth, Bankr/Telegram connectivity, budget, LLM gateway |
| `stockforge preflight` | go/no-go checklist: "am I ready to flip dry-run off?" (exits non-zero until every live prerequisite is green) |
| `stockforge treasury` | the extraction view — claimed WETH, per-wallet/per-token production, stock-pair verification status, recent claims |
| `stockforge confirm-pair <0xtoken> [--note]` | mark a launch's stock-pairing manually verified after you check it on Bankr |

Full CLI:

| Command | What it does |
|---|---|
| `stockforge run` | start the async orchestrator loop (default) |
| `stockforge status` | print config + budget + per-wallet usage |
| `stockforge doctor` | fail-closed readiness check (auth, connectivity, Telegram, budget) |
| `stockforge preflight` | pre-live checklist — "are you ready to flip dry-run off?" (fail-closed) |
| `stockforge treasury` | extracted fees + per-wallet/per-token production + pair-verification + compute funding |
| `stockforge confirm-pair <0xtoken> [--note]` | mark a stock-pairing manually verified |
| `stockforge selfcheck [TICKER] [--chain] [--live-approval]` | run a full **dry-run** pipeline end to end |
| `stockforge preview <TICKER> [--chain]` | forge a concept + show the exact Bankr request, no broadcast |
| `stockforge launch <TICKER> [--chain]` | **one** controlled launch — respects dry-run + Telegram approval |
| `stockforge promo <TICKER>` | generate a full launch **copy package** (draft; nothing posted) |
| `stockforge fees <0xtoken>` | read fees for a token (public, no auth) |

Telegram mirrors these: `/status /launch /promo /claim /treasury /confirmpair
/pause /resume` plus Approve/Reject buttons on every real launch or claim.

## Dual-mode launching (stock-paired **and** standard)

StockForge supports both launch paths as first-class — it is **not** stock-only,
so it never dies if pairing is weak, restricted, or unavailable.

| `STOCKFORGE_LAUNCH_MODE` | Behavior |
|---|---|
| `auto` (default) | Try a stock pair when it makes sense (robinhood chain + a recognized watchlist stock); **on failure, retry once as a standard launch** |
| `stock_paired` | Prefer a stock pair when it's actually possible; otherwise a standard launch (never a fabricated pairing) |
| `standard` | Normal pool, never request a stock pair |

**Routing.** A launch only requests a stock pair when the mode allows it **and**
the chain is `robinhood` **and** the ticker is a recognized stock (in
`STOCKFORGE_WATCHLIST`). Non-stock / generic narratives route to a standard
launch automatically. No Bankr stock-pair parameter is fabricated — pairing is
expressed via the NL prompt and the outcome is classified after the fact.

**Force a mode per launch:**
```bash
stockforge launch NVDA --chain robinhood --mode stock     # force stock-paired
stockforge launch NVDA --mode standard                    # force standard
stockforge preview NVDA --chain robinhood --mode auto     # see what would be sent
```

**Outcomes are recorded** on every launch: `launch_mode` (requested: auto/
stock_paired/standard), `pair_status` (`accepted / degraded / rejected /
requested / not_requested`), and `final_mode` (stock-pair vs standard). In `auto`
mode a failed stock-pair launch is followed by a `↘️ … retrying STANDARD` and a
second recorded standard attempt. `stockforge treasury` shows the split:
`by kind: stock-paired=N standard=M`.

## Human Verification Gate

**Do these in order on a real machine. Dry-run stays ON until the last step.**
Nothing here spends money except the one deliberate Bankr launch in step 2.

1. **Readiness — `stockforge doctor` then `stockforge preflight`.** Both must be
   green (`preflight` must reach `✅ READY FOR LIVE`; it exits non-zero and prints
   `⛔ NOT READY FOR LIVE` until every live prerequisite is present).
2. **Do ONE real stock-paired launch on Bankr yourself** (Bankr UI/CLI), on
   Robinhood Chain, and confirm the pool is quoted in the stock. This is the only
   way to close the `pair_status` unknown — StockForge cannot verify the pairing
   contract for you.
3. **Record it — `stockforge confirm-pair <0xtoken> --note "pool quoted in NVDA"`.**
   This marks that pairing verified and clears it from the pending list.
4. **Verify the money view — `stockforge treasury`.** Confirm the token shows up,
   the pairing reads as confirmed (`✅pair`), and (once fees accrue + a claim runs)
   claimed WETH is tracked correctly.
5. **Only then** consider flipping `STOCKFORGE_DRY_RUN=false` — start with
   `STOCKFORGE_DAILY_LAUNCH_BUDGET=1` and keep `STOCKFORGE_REQUIRE_APPROVAL=true`
   for the first real launch. See [`CLAUDE.md`](CLAUDE.md) for the full
   go-live checklist.

Until step 5, every launch is simulated and nothing broadcasts.

## Architecture

```
src/stockforge/
  config.py         # env-only settings; safe defaults; secret redaction
  models.py         # Signal → Concept → LaunchRequest → LaunchResult → FeeSnapshot
  db.py             # async SQLite state (WAL); daily counters
  ratelimit.py      # Bankr 50/100-day + 1/min, counts failures; token bucket
  circuit.py        # circuit breaker around Bankr calls
  signal/           # attention scoring + signal sources (news RSS, watchlist, manual)
  forge/            # concept generation (template or LLM) + anti-slop/uniqueness
  launcher/         # Bankr CLI + REST backends, unified facade, dry-run switch
  fees/             # public fee reader + claimer (build-claim / wallet CLI)
  promo/            # launch announcement composition (human-gated posting)
  orchestrator/     # main loop + Telegram control plane (commands + approvals)
  cli.py            # entrypoint
prompts/            # concept/tweet/image/scoring prompts
skills/             # Bankr + operating playbooks (verified endpoints)
tests/              # ratelimit, anti-slop, circuit, dry-run launcher, forge
```

## Bankr integration (verified against docs.bankr.bot, July 2026)

- **Launch (REST):** `POST https://api.bankr.bot/agent/prompt` with
  `X-API-Key`, body `{"prompt": "deploy a token called X on base"}` (async job).
- **Launch (CLI):** `bankr --ni launch --name … --symbol … --chain robinhood …`
  (`--simulate` maps to dry-run).
- **Fees (public, no auth):** `GET /public/doppler/token-fees|claimable-fees|creator-fees`,
  `POST /public/doppler/build-claim` (unsigned txs).
- **Limits:** 50/day (Standard), 100/day (Club), 1/min, failed attempts count.
  Fee split 95% creator / 5% protocol; 0.7% swap fee.

See [`skills/bankr-launch.md`](skills/bankr-launch.md) for the full contract.

### ⚠️ Two honest caveats (do not skip)

1. **`POST /token-launches/deploy` is not a real Bankr endpoint.** The original
   brief assumed it; the actual launch path is `POST /agent/prompt` (natural
   language) or the CLI. StockForge builds against the real endpoints and keeps
   `BANKR_API_BASE` / paths env-configurable.
2. **Stock-pairing is live on Bankr but its API/CLI contract is soft.** Pools
   quoted in the stock (NVDA/GME/TSLA) exist today on **Robinhood Chain**, but no
   documented parameter exposes it. StockForge expresses the pair via the Agent
   NL prompt (`… paired with NVDA on robinhood chain`), **only on the `rest`
   backend** (the `bankr launch` CLI has no pairing flag — we do not invent one),
   and **classifies the outcome** rather than assuming it worked:

   | `pair_status` | meaning |
   |---|---|
   | `accepted` | pool is quoted in the requested stock |
   | `degraded` | launched, but paired standard (WETH) instead |
   | `rejected` | launch failed |
   | `requested` | asked for, not yet verifiable (dry-run / pending) |
   | `not_requested` | standard launch |

### Verify a stock-paired launch manually

```bash
# 1. Preview the exact prompt (no broadcast):
stockforge preview NVDA --chain robinhood
#    -> prompt: deploy a token called "…" with symbol … paired with NVDA on robinhood chain

# 2. Full dry-run pipeline incl. pairing classification + approval flow:
stockforge selfcheck NVDA --chain robinhood --live-approval

# 3. After a REAL launch (dry-run off, rest backend, robinhood chain), confirm
#    the launcher logged  pair_status=accepted  and read the pool back:
stockforge fees <0xtoken>     # token0Label/token1Label should show the stock
```
If `pair_status` comes back `degraded`, Bankr didn't honor the pairing — the
token still launched against a standard pool (safe degradation), so treat it as
a WETH launch and open a Bankr support ticket to confirm the pairing contract.

### Before you turn off dry-run

Run `stockforge doctor` (must exit 0, no ❌) and confirm the checklist in
[`CLAUDE.md`](CLAUDE.md#do-not-turn-off-dry-run-until-all-of-these-are-true):
real Bankr auth, beneficiary set, Telegram `getMe`/`getChat` passing,
`DAILY_LAUNCH_BUDGET=1`, a dedicated funded hot wallet, and a
`selfcheck --live-approval` where the buttons actually worked.

## Path to First Live Launch

Follow this in order. **Dry-run stays ON the entire time until the last step.**

1. **Readiness.** `stockforge doctor` (config sanity) and `stockforge preflight`
   (live prerequisites). `preflight` exits non-zero and prints
   `⛔ NOT READY FOR LIVE` until every critical item is green — by design.
2. **Manually verify stock-pairing on Bankr yourself.** Do one real paired launch
   through Bankr's own UI/CLI and confirm the pool is quoted in the stock. This is
   the only way to close the `pair_status` unknown — StockForge cannot verify the
   pairing contract for you.
3. **Fill real `.env`, keep `STOCKFORGE_DRY_RUN=true`.** Set `BANKR_API_KEY`,
   `BANKR_BENEFICIARY_ADDRESS`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Use a
   dedicated, minimally-funded hot wallet — never a main key.
4. **Test the full pipeline + approvals in dry-run.**
   `stockforge selfcheck --live-approval` (buttons must work), then
   `stockforge launch NVDA --chain robinhood` for a single dry-run launch and
   confirm the `launch_record` JSON line looks right.
5. **Only then** set `STOCKFORGE_DAILY_LAUNCH_BUDGET=1`, flip
   `STOCKFORGE_DRY_RUN=false`, and do ONE controlled launch with
   `stockforge launch <TICKER>` — it will require a Telegram approval tap before
   anything broadcasts.

Every launch attempt (dry-run included) emits a secret-free `launch_record` JSON
line to stdout and is persisted to SQLite: timestamp, name, ticker, requested
pair, final mode (stock-pair / standard), dry-run flag, approval status, and a
Bankr response summary. Every fee claim emits a matching `claim_record`.

## Continuous fee faucet (autonomous mode)

`stockforge run` is a non-stop engine: each tick it finds the strongest stock
narrative, launches (preferring stock-paired on Robinhood Chain), routes creator
fees to one **treasury** address, and periodically claims those fees. It turns
attention into a continuous fee stream — within hard limits.

Two `.env` switches control how hands-off it runs (both default to safe):

| Flag | Safe default | Faucet mode | Effect |
|---|---|---|---|
| `STOCKFORGE_DRY_RUN` | `true` | `false` | `false` = real broadcasts |
| `STOCKFORGE_REQUIRE_APPROVAL` | `true` | `false` | `false` = **autonomous**, no Telegram tap |

Fee routing / claiming:

| Flag | Default | Purpose |
|---|---|---|
| `STOCKFORGE_TREASURY_ADDRESS` | = beneficiary | single fee recipient + claim destination |
| `STOCKFORGE_AUTO_CLAIM` | `true` | sweeps auto-claim once above the threshold |
| `STOCKFORGE_FEE_CLAIM_MIN_WETH` | `0.001` | skip dust claims (save gas) |
| `STOCKFORGE_FEE_SWEEP_EVERY_TICKS` | `6` | sweep cadence |

**Always-on rails even in autonomous mode:** daily budget (`STOCKFORGE_DAILY_LAUNCH_BUDGET`),
Bankr's 1-launch/minute + 50/100-day caps, the circuit breaker, and the `/pause`
Telegram kill switch. Autonomous on-chain claiming needs `BANKR_PRIVATE_KEY` (a
minimally-funded hot wallet); without it, claims are built as unsigned txs to sign.

> Going autonomous is opt-in and deliberate. Do the full
> [Path to First Live Launch](#path-to-first-live-launch) in dry-run first, then
> flip `STOCKFORGE_REQUIRE_APPROVAL=false` with a small budget.

### Aggressive continuous mode (within Bankr's limits)

Once verified, this `.env` runs the engine hard while still respecting every
hard rail. It maximizes fee capture **within a single Bankr account's caps** —
it does not, and must not, try to exceed them (Bankr restricts accounts for
high-volume/sybil deploys).

```bash
STOCKFORGE_DRY_RUN=false               # you flip this — never the agent
STOCKFORGE_REQUIRE_APPROVAL=false      # autonomous, no Telegram tap
STOCKFORGE_NEWS_SOURCE=true            # real attention drives launches
STOCKFORGE_DEFAULT_CHAIN=robinhood     # prefer stock-paired (UNVERIFIED, degrades safely)
BANKR_BACKEND=rest                     # only rest can request a stock pair
STOCKFORGE_DAILY_LAUNCH_BUDGET=50      # <= Bankr's 50/day (100 for Club); still 1/min
STOCKFORGE_MIN_ATTENTION_SCORE=60      # lower = more launches (more marginal quality)
STOCKFORGE_TICK_SECONDS=60             # 1 tick/min matches the 1/min launch limit
STOCKFORGE_AUTO_CLAIM=true             # consolidate fees to the treasury automatically
BANKR_PRIVATE_KEY=0x...                # hot wallet — required for autonomous claiming
STOCKFORGE_TREASURY_ADDRESS=0x...      # where all fees consolidate
STOCKFORGE_PER_WALLET_DAILY_CAP=50     # each wallet respects Bankr's per-account cap
# Optional: one operation, several wallets (opsec / treasury split / SPOF)
STOCKFORGE_WALLETS='[{"id":"a","fee_recipient":"0x..","api_key":"bk_.."},{"id":"b","fee_recipient":"0x.."}]'
# Optional: fees pay for the agent's own compute
FORGE_LLM_PROVIDER=bankr               # concept generation via the Bankr LLM Gateway
```

The `/pause` Telegram kill switch, the global daily budget, per-wallet 1/min +
50/100-day caps, and the circuit breaker all still apply. Track extracted value
with `stockforge treasury` or `/treasury`.

### Multi-wallet: one honest operation, several wallets

`STOCKFORGE_WALLETS` (JSON) runs the engine across a pool of wallets for
legitimate operational reasons — **key segregation / opsec, treasury splitting,
reduced single-point-of-failure, and respecting each wallet's own Bankr rate
limit.** Launches distribute least-recently-used; each wallet independently
enforces Bankr's 50/100-per-day + 1/min, and `STOCKFORGE_DAILY_LAUNCH_BUDGET` is
the global hard ceiling across all of them. Every launch is **attributed** to its
wallet (visible in `launch_record`, `/status`, and `stockforge treasury`), and
fees route to each wallet's recipient (default: the treasury, so they
consolidate).

This is explicitly **not** a disguise: the wallets are disclosed as one
operation, attribution is tracked openly, and StockForge does not build machinery
to make launches look like unrelated creators or to multiply past Bankr's
per-account caps (that's sybil behavior Bankr restricts — its documented path for
genuine high volume is a support ticket).

### Promotion (operator-gated)

On each launch, StockForge composes a **promo kit** and sends it to you for review
(Telegram + logs). It **never auto-posts to public social**; a `Publisher` seam
lets a real X poster / content agent plug in later. The "not affiliated with
<TICKER>" disclaimer is always included. Toggle with `STOCKFORGE_PROMO_ENABLED`.

The **full copy package** — grab it anytime with `stockforge promo <TICKER>` or
`/promo <TICKER>` — contains:

- **Tweet** — the launch post (≤270 chars, CA + link + disclaimer).
- **Narrative** — 2–3 sentences for a longer post / thread.
- **Hashtags/cashtags** — `$SYMBOL` + `$TICKER`.
- **Follow-ups** — a couple of reminder drafts to post later at your discretion.

This gives a launch a real chance at attention while keeping you in control: the
system drafts, **you** post. It never auto-spams. Use `stockforge launch` +
`stockforge promo` to push a launch and grab its copy in two quick commands.

### Reading the extraction view

`stockforge treasury` (or `/treasury`) is the at-a-glance capital view:

- **💰 CLAIMED (recorded)** — total WETH your successful claims covered.
- **wallets** — per-wallet launch attribution (which wallet is producing).
- **top producing tokens** — launched tokens ranked by claimable WETH (a `✅pair`
  marks a confirmed stock-pairing).
- **stock-pair verification** — how many pairings are confirmed vs still pending a
  manual check, with the exact `confirm-pair` command for each pending token.
- **recent extraction activity** — the last few claims with wallet + mode.

Fee sweeps read each token's claimable **against the address it routes to** (per
wallet), and claim per wallet: a wallet with its own `private_key` claims with
that key; otherwise unsigned txs are built for you to sign. Every claim is
attributed and recorded secret-free.

### Confirming a stock-pair (closing the UNVERIFIED gap)

Stock-pairing has no documented Bankr parameter, so a launch's pairing stays
`requested` (unverified) until you check it. After confirming on Bankr that the
pool is really quoted in the stock:

```bash
stockforge confirm-pair 0xToken --note "pool quoted in NVDA on Bankr"
# or in Telegram:  /confirmpair 0xToken pool quoted in NVDA
```

This persists the confirmation and clears the token from the pending-verification
list. Until confirmed, treat a stock pair as a standard launch.

### Fees → compute (self-funding loop)

`FORGE_LLM_PROVIDER=bankr` routes the concept forge through the **Bankr LLM
Gateway** (`llm.bankr.bot`, OpenAI-compatible), so trading fees fund the agent's
own compute — Bankr's official loop: *wallet → launch → fees → claim → pay for
compute → keep running*. LLM credits are USD and separate from the trading
wallet; top up on a funded account (`bankr llm credits add 25`, or
`bankr llm credits auto --enable`). StockForge never auto-buys credits — that
stays a deliberate human step.

## Deploy (Zeabur / Docker)

```bash
docker compose up --build          # local
```

- `Dockerfile` installs Python + the `@bankr/cli` (for the CLI backend).
- `zeabur.json` defines the service, a `/data` volume for SQLite, and safe env
  defaults. No inbound ports needed — the control plane is Telegram long-poll.

## Testing

```bash
pip install -r requirements-dev.txt
pytest -q          # 58 tests: ratelimit, anti-slop, circuit, dry-run, forge, news, treasury/claims
ruff check src tests
```

## License

UNLICENSED — internal Phantom Capital project.
