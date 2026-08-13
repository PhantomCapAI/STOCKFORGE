# Attention Scoring — Reference

The built-in `AttentionScorer` (src/stockforge/signal/scorer.py) is a
transparent weighted heuristic. This doc is the spec so a future model-backed
scorer stays compatible.

**Contract:** `score(signal) -> float` in `[0, 100]`.

## Inputs a signal can carry
- `ticker` — the underlying stock.
- `headline` — short text describing the narrative.
- `sources` — list of independent sources that surfaced it (breadth = realness).
- `meta.magnitude` — optional externally-computed intensity (0–20): % price move,
  mention-volume z-score, unusual options flow, etc.

## Weighting (current heuristic)
| Factor | Max points | Rationale |
|---|---|---|
| Source breadth (min(#sources,5) × 8) | 40 | Independent corroboration = real attention |
| Narrative keywords in headline (min(hits,4) × 9) | 36 | squeeze/halt/earnings/ATH/etc. = live story |
| Ticker prior | ~15 | Some tickers reliably carry retail energy |
| External magnitude (meta) | 20 | Quant signal when available |

## Launch gate
A candidate is launch-eligible only when
`attention_score >= STOCKFORGE_MIN_ATTENTION_SCORE` (default 65). Baseline
watchlist pings score low on purpose — a real source or a manual `/launch` must
supply genuine evidence to cross the line.

## Upgrade path
Swap the heuristic for a real signal fusion (X/Twitter trends API, news
sentiment, on-chain flow) by implementing a new `SignalSource.poll()` and/or a
new scorer with the same contract. The orchestrator does not change.
