"""Backend contract + shared prompt/param builders for Bankr launches.

Kept backend-agnostic so the CLI and REST implementations stay thin and the
orchestrator never cares which one is wired in.
"""

from __future__ import annotations

from typing import Protocol

from ..models import LaunchRequest, LaunchResult
from .pairing import stock_pair_phrase


class LaunchBackend(Protocol):
    """Anything that can turn a LaunchRequest into a LaunchResult."""

    async def launch(self, req: LaunchRequest) -> LaunchResult: ...


def build_launch_prompt(req: LaunchRequest) -> str:
    """Natural-language deploy instruction for the Agent API / `bankr agent`.

    Bankr accepts free-form deploy prompts. The verified base form is
    "deploy a token called X with symbol Y on <chain>". When a stock pair is
    requested we express it in the phrasing Bankr's agent understands today, e.g.
    '... paired with NVDA on robinhood chain'.

    NOTE: stock-pairing is NOT a documented first-class Bankr parameter. It is
    expressed as intent; if Bankr's Robinhood Chain honors it the pool is quoted
    in the stock, otherwise the launch degrades to a standard (WETH) pool. The
    outcome is classified after the fact (see pairing.classify_pairing).
    """
    parts = [f'deploy a token called "{req.name}" with symbol {req.symbol}']
    # Chain + pairing are expressed together so the intent reads naturally.
    if req.pair_with:
        parts.append(stock_pair_phrase(req.pair_with, req.chain))
    else:
        parts.append(f"on {req.chain}")
    if req.image_url:
        parts.append(f"image {req.image_url}")
    if req.website:
        parts.append(f"website {req.website}")
    if req.tweet_url:
        parts.append(f"tweet {req.tweet_url}")
    if req.fee_recipient:
        parts.append(f"route fees to {req.fee_recipient}")
    if req.disable_vesting:
        parts.append("with no vesting")
    return " ".join(parts)


def build_cli_args(req: LaunchRequest, simulate: bool) -> list[str]:
    """Argument vector for `bankr launch ...` (headless, non-interactive)."""
    args = ["launch", "--name", req.name, "--symbol", req.symbol, "--chain", req.chain]
    if req.image_url:
        args += ["--image", req.image_url]
    if req.tweet_url:
        args += ["--tweet", req.tweet_url]
    if req.website:
        args += ["--website", req.website]
    if req.fee_recipient:
        args += ["--fee", req.fee_recipient]
        if req.fee_recipient_type:
            args += ["--fee-type", req.fee_recipient_type]
    if req.disable_vesting:
        args += ["--no-vesting"]
    # `--simulate` builds the tx without broadcasting (dry-run safety).
    if simulate:
        args += ["--simulate"]
    else:
        args += ["--yes"]
    return args
