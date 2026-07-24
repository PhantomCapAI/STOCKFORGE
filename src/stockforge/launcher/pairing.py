"""Stock-pairing intent: phrasing + best-effort outcome classification.

Bankr does NOT publicly document a first-class "pair against a stock" parameter.
Stock-paired launches are live on Bankr today (pools quoted in NVDA/GME/TSLA on
Robinhood Chain), but the exact public API/CLI contract is soft. So we:

  1. Express the intent in the natural-language form Bankr's agent understands
     today (the Agent API / `bankr agent` NL prompt).
  2. Classify the OUTCOME from the launch response, honestly, into
     accepted / degraded / rejected / requested(unknown) — never assume success.

Important: the `bankr launch` CLI exposes no pairing flag in the verified docs
(`--name --symbol --chain --image --tweet --website --fee --fee-type
--no-vesting --simulate --yes`). We do NOT fabricate one. The CLI path therefore
cannot request stock-pairing today — only the Agent/NL path can.
"""

from __future__ import annotations

from typing import Any

from ..models import LaunchStatus, PairStatus

# The `bankr launch` CLI has no documented pairing flag. Keep this False until a
# real flag is verified in the Bankr docs — do not invent `--pair`.
CLI_SUPPORTS_STOCK_PAIR = False

# Assets a "standard" (non-stock) pool is quoted in. If the pool came back quoted
# in one of these and NOT the requested stock, the pairing degraded.
STANDARD_QUOTE_ASSETS = {"WETH", "ETH", "USDC", "USDT", "DAI"}

# Keys in a launch/job response that tend to carry the pool's quote asset label.
_LABEL_KEYS = (
    "token0Label",
    "token1Label",
    "quoteToken",
    "quoteSymbol",
    "quoteLabel",
    "pairedAsset",
    "pairedWith",
    "pair",
    "quote",
)


def stock_pair_phrase(ticker: str, chain: str) -> str:
    """The natural-language fragment expressing the pairing intent.

    Best-known phrasing for Bankr's agent today, e.g.
    'paired with NVDA on robinhood chain'. Stock-pairing is a Robinhood Chain
    feature; on Base we just say 'on base' (no pairing).
    """
    ticker = ticker.upper()
    if chain == "robinhood":
        return f"paired with {ticker} on robinhood chain"
    return "on base"


def find_quote_labels(raw: Any) -> list[str]:
    """Best-effort scan of a response for pool quote-asset labels."""
    found: list[str] = []
    stack = [raw]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k in _LABEL_KEYS and isinstance(v, str) and v:
                    found.append(v.upper())
                elif isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    # De-dupe, preserve order.
    seen: set[str] = set()
    out = []
    for label in found:
        if label not in seen:
            seen.add(label)
            out.append(label)
    return out


def classify_pairing(
    requested_ticker: str,
    status: LaunchStatus,
    token_address: str,
    labels: list[str],
) -> PairStatus:
    """Classify the pairing outcome from what the launch response actually shows.

    Honest and conservative: we only claim ACCEPTED/DEGRADED when there is label
    evidence; otherwise the outcome is REQUESTED (asked for, not yet verifiable —
    e.g. dry-run, pending job, or a response without pool labels).
    """
    if not requested_ticker:
        return PairStatus.NOT_REQUESTED
    if status in (LaunchStatus.FAILED, LaunchStatus.REJECTED):
        return PairStatus.REJECTED

    want = requested_ticker.upper()
    labs = [label.upper() for label in labels]
    if any(want == label or want in label.split("/") for label in labs):
        return PairStatus.ACCEPTED
    if labs and all(label not in (want,) for label in labs) and any(
        label in STANDARD_QUOTE_ASSETS for label in labs
    ):
        return PairStatus.DEGRADED
    # No conclusive label evidence — asked for it, can't confirm yet.
    return PairStatus.REQUESTED
