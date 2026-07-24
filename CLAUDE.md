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
- ⚠️ **Stock-pairing** (pool paired with NVDA/GME instead of WETH) is **not
  documented**. It's passed as a natural-language + field intent, only on
  Robinhood Chain, and degrades to WETH pairing. Before claiming it works,
  confirm with Bankr and wire the exact parameter in `launcher/base.py`.
- ⚠️ The Agent API **job-polling path** isn't fully specified publicly. The REST
  backend tries `/agent/job/{id}`, treats 404 as "leave SUBMITTED", and never
  fabricates a token address.

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
