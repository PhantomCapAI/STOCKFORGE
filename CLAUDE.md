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

## Verify before going live

- `stockforge doctor` — fail-closed readiness check: dry-run flag, Bankr auth +
  reachability, CLI installed/authenticated (`bankr whoami`), Telegram token +
  chat reachability (`getMe`/`getChat`), rate-limiter/budget state. Exits
  non-zero on any ❌.
- `stockforge selfcheck [TICKER] [--chain robinhood] [--live-approval]` — runs a
  full **dry-run** pipeline (signal → concept → launch(sim) → fee check →
  approval flow) so a human can verify everything without spending a launch.
  Refuses to run if `dry_run=false`.

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

## Hard rules (safety > features)

1. **Dry-run is the default.** `STOCKFORGE_DRY_RUN=true` means nothing broadcasts.
   The REST backend refuses to broadcast in dry-run; the CLI backend uses
   `--simulate`. Never remove this guard.
2. **Secrets only in `.env`.** No keys in code, logs, commits, or PRs. Use
   `Settings.redacted()` for any config you log. `.env` is gitignored.
3. **Human-in-the-loop for money.** Real launches and claims require Telegram
   approval. No Telegram configured ⇒ **fail-closed** (deny).
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
| Change launch policy | `.env` (`STOCKFORGE_*`) — no code change |

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
