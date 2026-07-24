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

CLI:

| Command | What it does |
|---|---|
| `stockforge run` | start the async orchestrator loop (default) |
| `stockforge status` | print config + today's launch budget |
| `stockforge doctor` | fail-closed readiness check (auth, connectivity, Telegram, budget) |
| `stockforge selfcheck [TICKER] [--chain] [--live-approval]` | run a full **dry-run** pipeline end to end |
| `stockforge preview <TICKER> [--chain]` | forge a concept + show the exact Bankr request, no broadcast |
| `stockforge fees <0xtoken>` | read fees for a token (public, no auth) |

## Architecture

```
src/stockforge/
  config.py         # env-only settings; safe defaults; secret redaction
  models.py         # Signal → Concept → LaunchRequest → LaunchResult → FeeSnapshot
  db.py             # async SQLite state (WAL); daily counters
  ratelimit.py      # Bankr 50/100-day + 1/min, counts failures; token bucket
  circuit.py        # circuit breaker around Bankr calls
  signal/           # attention scoring + signal sources (watchlist, manual)
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
pytest -q          # 23 tests: ratelimit, anti-slop, circuit, dry-run, forge
ruff check src tests
```

## License

UNLICENSED — internal Phantom Capital project.
