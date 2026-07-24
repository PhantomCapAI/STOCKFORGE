# StockForge — what this build is (in plain terms)

**StockForge is a bot that turns trending stock news into crypto tokens on
[Bankr](https://bankr.bot), promotes them, and collects the trading fees — then
uses those fees to pay for its own running costs.** A human approves every real
money move over Telegram.

> 🛡️ **It ships in "dry-run" mode.** Nothing touches the blockchain until you
> deliberately turn dry-run off. Until then every launch is *simulated* and only
> logged. Secrets live only in `.env`.

## How it works (one loop, every tick)

1. **Watch** — scan trending stocks (NVDA, GME, TSLA…) for real attention.
2. **Score** — rate how hot each narrative is, 0–100. Only strong ones pass.
3. **Forge** — invent an original token concept (name, symbol, thesis); reject
   anything derivative/sloppy.
4. **Gate** — respect Bankr's rate limits + your daily budget, then ask a human
   in Telegram to **Approve/Reject** (real launches only).
5. **Launch** — deploy the token via Bankr (paired with the stock on Robinhood
   Chain, or standard on Base), then **claim the trading fees** and repeat.

## The commands (CLI functions)

| Command | What it does |
|---|---|
| `stockforge run` | Start the autonomous loop (dry-run by default) |
| `stockforge status` | Show config + today's launch budget |
| `stockforge doctor` | Readiness check — auth, connectivity, Telegram, budget (safe) |
| `stockforge preflight` | "Am I ready to turn dry-run off?" go/no-go checklist |
| `stockforge preview <TICKER>` | Forge + show the exact Bankr request, **nothing sent** |
| `stockforge selfcheck [TICKER] [--live-approval]` | Run the whole pipeline in dry-run end-to-end |
| `stockforge launch <TICKER> [--chain]` | **One** controlled launch (respects dry-run + approval) |
| `stockforge fees <0xtoken>` | Read a token's fees (public, no auth) |

## The Telegram controls

`/status` · `/launch <TICKER>` · `/claim` · `/pause` · `/resume` · `/help`
— plus **Approve / Reject** buttons on every real launch or fee claim.

## The pieces (what each folder does)

| Module | Job |
|---|---|
| `config.py` | All settings from `.env`; safe defaults; hides secrets in logs |
| `models.py` | The data shapes: Signal → Concept → Launch → Fees |
| `db.py` | Async SQLite memory (state, daily counters, records) |
| `signal/` | Find + score attention. **Real source: live financial-news volume via Google News (no API key).** Plus a manual `/launch` queue |
| `forge/` | Generate the token concept + anti-slop/uniqueness check |
| `launcher/` | Talk to Bankr (REST or CLI); the hard **dry-run switch**; stock-pairing |
| `fees/` | Read + claim creator fees |
| `promo/` | Compose launch announcements (human-gated) |
| `orchestrator/` | The main loop + Telegram control plane |
| `ratelimit.py` / `circuit.py` | Respect Bankr limits; stop after repeated failures |
| `cli.py` | The `stockforge` command entrypoint |

## Safety rails (always on)

- **Dry-run by default** — no broadcasts until you flip it off on purpose.
- **Human-in-the-loop** — real launches/claims need a Telegram tap; no Telegram = everything denied (fail-closed).
- **Rate limit + circuit breaker** — never exceed Bankr's caps; back off after 3 failures.
- **Every attempt is recorded** — a secret-free JSON `launch_record` to stdout + SQLite.

See [`README.md`](README.md) for full detail and [`CLAUDE.md`](CLAUDE.md) for the
go-live checklist. **Do not turn off dry-run** until that checklist is fully green.
