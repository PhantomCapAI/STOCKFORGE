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

## Stock-pairing (NVDA/GME) — ⚠️ UNVERIFIED
Bankr's documented pools pair the new token against **WETH** on a Uniswap V4
pool (fee reads show `token0Label: WETH`). A first-class "pair against NVDA"
parameter is **not documented** in the public docs we verified. StockForge
therefore:
- Only sets `pair_with` when `chain=robinhood`.
- Passes the pairing as a natural-language hint ("... paired with NVDA") and an
  explicit `LaunchRequest.pair_with` field.
- Degrades gracefully to standard WETH pairing if Bankr ignores the hint.

**Before relying on stock-pairing in production, confirm the capability with
Bankr support / current docs and wire the exact parameter here.**

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
