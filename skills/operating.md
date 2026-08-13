# Skill: Operating StockForge

## First run (safe, no money)
```bash
cp .env.example .env            # fill in Telegram + (optionally) Bankr keys
pip install -r requirements.txt
pip install -e .

stockforge doctor               # readiness check
stockforge preview NVDA         # forge + preview a launch, nothing sent
stockforge run                  # start the loop (DRY-RUN by default)
```
With `STOCKFORGE_DRY_RUN=true` (the default) nothing hits the chain. Launches are
simulated and logged; you still get the full pipeline + Telegram messages.

## Going live (deliberate)
1. Fund a **dedicated hot wallet** minimally. Put its key in `BANKR_PRIVATE_KEY`
   only if you want headless claims; otherwise leave blank and sign build-claim
   txs yourself.
2. Set `BANKR_API_KEY` and `BANKR_BENEFICIARY_ADDRESS`.
3. Configure `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (approvals are
   **fail-closed** — no Telegram = every real action denied).
4. Set `STOCKFORGE_DRY_RUN=false`, keep `STOCKFORGE_REQUIRE_APPROVAL=true`.
5. Start small: `STOCKFORGE_DAILY_LAUNCH_BUDGET=1`.

## Telegram commands
| Command | Effect |
|---|---|
| `/status` | health, budget used/remaining, circuit state |
| `/launch <TICKER> [headline]` | queue a manual candidate (still scored + gated) |
| `/claim` | run a fee sweep + (approved) claim now |
| `/pause` / `/resume` | halt / resume the pipeline |
| Approve/Reject buttons | decide a pending launch or claim |

## Safety model
- **Dry-run master switch** — off by default in effect; nothing broadcasts.
- **Circuit breaker** — 3 consecutive Bankr failures opens it for 5 min.
- **Rate limiter** — mirrors Bankr's 50/100-per-day + 1/min, counts failures.
- **Human-in-the-loop** — real launches and claims require a Telegram tap.
- **Fail-closed** — approval timeout or missing Telegram = deny.
- **Secrets** — only in `.env`; never logged (see `Settings.redacted()`).
