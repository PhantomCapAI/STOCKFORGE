# Skill: Launching + Claiming on Bankr

Source of truth: <https://docs.bankr.bot> (verified July 2026). This file records
exactly what StockForge relies on so nobody has to re-guess the API.

## Auth
- REST Agent API key `bk_...` → `BANKR_API_KEY`, sent as header `X-API-Key`.
- Get one at <https://bankr.bot/api>.
- CLI reads `~/.bankr/config.json` or `BANKR_API_KEY` / `BANKR_PRIVATE_KEY` from
  the environment. Headless login: `bankr login siwe --private-key 0x...` or
  `bankr login email <addr> --code <otp> --accept-terms --agent-api --read-write`.

## Launch — REST (what StockForge's `rest` backend uses)
```
POST https://api.bankr.bot/agent/prompt
X-API-Key: bk_...
{ "prompt": "deploy a token called \"Silicon NVDA\" with symbol SILNV on base" }
```
Async, job-based. StockForge submits the prompt, then polls the job for the
deployed contract address / pool. It **never fabricates** an address.

> The task brief assumed `POST /token-launches/deploy`. That is **not** the real
> Bankr endpoint — the real path is `POST /agent/prompt` (natural language).
> Base URL + paths are env-configurable (`BANKR_API_BASE`) if Bankr adds a
> structured deploy route later.

## Launch — CLI (`cli` backend)
```
bankr --ni launch --name "Silicon NVDA" --symbol SILNV \
  --chain robinhood --image <url> --tweet <url> --website <url> \
  --fee "@partner" --fee-type x --yes
# dry-run / simulate (no broadcast):
bankr --ni launch --name "Silicon NVDA" --symbol SILNV --simulate
```
- `--chain`: `base` (CLI default) or `robinhood`.
- `--no-vesting` turns off the 15% creator vesting.
- `--simulate` builds the tx without broadcasting — StockForge maps `dry_run`
  onto this.

## Stock-pairing (NVDA/GME/TSLA) — ⚠️ live on Bankr, contract still soft
Stock-paired launches are **live on Bankr today** — pools quoted in the stock
(NVDA/GME/TSLA…) on **Robinhood Chain**. But the exact public API/CLI parameter
is **not documented**, so StockForge expresses it as intent and classifies the
outcome honestly rather than assuming success.

**Best-known phrasing (Agent API / `bankr agent` NL prompt), used today:**
```
deploy a token called "Silicon NVDA" with symbol SILNV paired with NVDA on robinhood chain
```
(see `stock_pair_phrase()` and `build_launch_prompt()` in `launcher/`.)

**CLI limitation — do NOT fake a flag.** The verified `bankr launch` flags are
`--name --symbol --chain --image --tweet --website --fee --fee-type
--no-vesting --simulate --yes`. There is **no pairing flag**. So the CLI backend
*cannot* request stock-pairing — only `BANKR_BACKEND=rest` (the Agent/NL path)
can. `CLI_SUPPORTS_STOCK_PAIR=False` in `launcher/pairing.py` enforces this; the
launcher logs a warning and the preview shows a note when a pair is requested on
the CLI backend.

**Outcome classification** (`launcher/pairing.py::classify_pairing`, best-effort
from the launch/job response labels):

| `pair_status` | Meaning |
|---|---|
| `not_requested` | standard (WETH) launch, no stock pair asked for |
| `requested` | asked for, **not yet verifiable** (dry-run, pending job, or no pool labels) |
| `accepted` | response shows the pool quoted in the requested stock |
| `degraded` | launched, but pool quoted in a standard asset (WETH/ETH/USDC…) instead |
| `rejected` | launch failed / no token produced |

Safe degradation is preserved: if Bankr doesn't honor the pairing, the token
still launches against a standard pool and we report `degraded` (never a silent
false "accepted").

**Remaining uncertainty (needs a human + a real launch to close):**
- The exact field/verb Bankr's agent expects for pairing (we use NL phrasing).
- Which response field carries the pool quote label on a stock-paired launch —
  `find_quote_labels()` scans common keys (`token0Label`, `quoteSymbol`,
  `pairedAsset`, …); confirm the real key from one live launch and tighten it.
- Whether a structured (non-NL) deploy/pairing endpoint exists for partners.

## Limits (enforced by `LaunchRateLimiter`)
- Standard: **50 deploys / 24h**; Bankr Club: **100 / 24h**.
- **1 deploy / minute.**
- **Failed attempts still count** against the daily cap.
- Gas sponsored for first 3/day (Standard) / 10/day (Club).

## Fees
Swap fee **0.7%**; split **95% creator / 5% protocol (Doppler)**. Supply 100B,
85% pool / 15% creator vesting (1yr, 30-day cliff) unless `--no-vesting`.

### Read (public, no auth)
```
GET  /public/doppler/token-fees/{token}?days=30
GET  /public/doppler/claimable-fees/{token}?beneficiary={addr}
GET  /public/doppler/creator-fees/{addr}?days=30
```
`token-fees` returns the `poolId` + `initializer` (Fees Manager) — cache them.

### Claim
- Headless (moves value): `bankr --ni fees claim-wallet --all` with
  `BANKR_PRIVATE_KEY`. StockForge only runs this when `dry_run=false`.
- No key on our side (safe default):
  ```
  POST /public/doppler/build-claim
  { "beneficiaryAddress": "0x...", "tokenAddresses": ["0x...", ...] }  # <=50
  ```
  Returns **unsigned** txs for a human/wallet to sign.
- On-chain: `collectFees(poolId)` on the `initializer` contract.
