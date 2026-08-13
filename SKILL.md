---
name: stockforge
description: Autonomous agent that turns trending stock news/attention into original crypto tokens on Bankr (prefer stock-paired on Robinhood Chain), launches them, claims creator fees, and uses those fees to fund its own compute. Dry-run by default with Telegram human-in-the-loop approvals.
tags: [token-launch, bankr, autonomous, stock-paired, fee-faucet, robinhood-chain]
version: 1
visibility: public
metadata:
  clawdbot:
    emoji: "⚒️"
    homepage: "https://github.com/PhantomCapAI/STOCKFORGE"
---

# StockForge

StockForge is a continuous, autonomous-capable fee-earning engine built on Bankr. It watches real stock attention (NVDA, GME, TSLA…), scores narratives, forges original token concepts (anti-slop), launches them (prefer stock-paired on Robinhood Chain, degrade gracefully to standard WETH pools), claims the 95% creator trading fees, and consolidates them into a single treasury that can pay for the agent’s own LLM/compute costs.

**Safety first:** ships in dry-run mode. Nothing hits the chain until you deliberately flip `STOCKFORGE_DRY_RUN=false`. Real launches and fee claims require Telegram Approve/Reject (fail-closed if Telegram is missing).

## When to use this skill

- User wants to run or operate an autonomous stock → token launch + fee claim loop on Bankr
- Need the exact Bankr launch / pairing / fee-claim endpoints and constraints that StockForge relies on
- Setting up, troubleshooting, or going live with the StockForge CLI / Telegram control plane
- Designing similar fee-faucet agents that stay inside Bankr’s rate limits and anti-sybil rules

## Core loop (every tick)

1. **Watch** — scan trending stocks for real attention (live financial-news volume via Google News; no API key required).
2. **Score** — rate narrative heat 0–100. Only strong ones pass the gate.
3. **Forge** — invent an original token concept (name, symbol, thesis). Reject derivative / sloppy ideas.
4. **Gate** — respect Bankr rate limits + daily budget, then ask human in Telegram to Approve/Reject (live only).
5. **Launch** — deploy via Bankr Agent API (NL prompt) or CLI. Prefer stock-paired on Robinhood Chain; report honest pairing outcome (`accepted` / `degraded` / etc.).
6. **Claim** — periodically sweep and claim creator fees to the treasury address.

## Key CLI commands

| Command | Purpose |
|---|---|
| `stockforge doctor` | Readiness check (auth, connectivity, Telegram, budget) |
| `stockforge preview <TICKER>` | Forge + show exact Bankr request (nothing sent) |
| `stockforge selfcheck [TICKER]` | Full dry-run pipeline |
| `stockforge run` | Start the autonomous loop (dry-run by default) |
| `stockforge launch <TICKER>` | One controlled launch |
| `stockforge fees <0xtoken>` | Read fees (public) |
| `stockforge status` / `preflight` | Budget + go-live checklist |

## Telegram control plane

`/status` · `/launch <TICKER>` · `/claim` · `/pause` · `/resume` · `/help`  
Plus **Approve / Reject** buttons on every real launch or fee claim.

## Bankr integration essentials (source of truth)

- **Auth:** Agent API key `bk_...` as `X-API-Key` header (`BANKR_API_KEY`).
- **Launch (REST / preferred for pairing):** `POST https://api.bankr.bot/agent/prompt` with natural-language prompt. Example pairing phrasing:
  ```
  deploy a token called "Silicon NVDA" with symbol SILNV paired with NVDA on robinhood chain
  ```
- **Launch (CLI):** `bankr --ni launch --name ... --symbol ... --chain robinhood|base --simulate` (no native pairing flag — only REST/NL path supports stock pairing cleanly).
- **Limits:** 50 deploys/24h (Standard) or 100 (Club); hard 1 deploy/minute; failed attempts still count.
- **Fees:** 0.7% swap fee, 95% creator / 5% protocol. Read via public Doppler endpoints; claim via `bankr --ni fees claim-wallet` or build-claim (unsigned) + human sign.
- **Stock-pairing status classification:** `not_requested` | `requested` | `accepted` | `degraded` | `rejected`. Never claim “accepted” without evidence from the response labels.

Full verified endpoint details, outcome classification logic, and remaining uncertainties live in the repo’s `skills/bankr-launch.md`.

## Safety rails (always on)

- Dry-run master switch (default true)
- Human-in-the-loop for real value movement (Telegram fail-closed)
- Rate limiter + circuit breaker (3 consecutive Bankr failures → 5 min open)
- Daily launch budget + per-wallet attribution
- Secrets only in `.env`; never logged
- Multi-wallet support is for legitimate opsec / rate-limit distribution only — disclosed as one operation, never for sybil volume

## Going live checklist (summary)

1. Fund a dedicated hot wallet minimally.
2. Set `BANKR_API_KEY`, `BANKR_BENEFICIARY_ADDRESS` / `STOCKFORGE_TREASURY_ADDRESS`.
3. Configure Telegram bot + chat ID.
4. Keep `STOCKFORGE_REQUIRE_APPROVAL=true` at first; start with `STOCKFORGE_DAILY_LAUNCH_BUDGET=1`.
5. Run `stockforge preflight` and `doctor` until green, then flip dry-run only when ready.

See the full repo (`OVERVIEW.md`, `CLAUDE.md`, `skills/operating.md`, `skills/bankr-launch.md`) for complete operator playbooks, env vars, and the exact go-live checklist.

**Repo:** https://github.com/PhantomCapAI/STOCKFORGE
