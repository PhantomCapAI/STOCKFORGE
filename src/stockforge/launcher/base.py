"""Backend contract + shared prompt/param builders for Bankr launches.

Kept backend-agnostic so the CLI and REST implementations stay thin and the
orchestrator never cares which one is wired in.
"""

from __future__ import annotations

from typing import Protocol

from ..models import LaunchRequest, LaunchResult


class LaunchBackend(Protocol):
    """Anything that can turn a LaunchRequest into a LaunchResult."""

    async def launch(self, req: LaunchRequest) -> LaunchResult: ...


def build_launch_prompt(req: LaunchRequest) -> str:
    """Natural-language deploy instruction for the Agent API / `bankr agent`.

    Bankr accepts free-form deploy prompts. We include the stock-pairing intent
    in-line. NOTE: stock-pairing (pairing against NVDA/GME rather than WETH) is
    NOT documented as a first-class Bankr parameter — it is passed as a hint so
    that IF Bankr's Robinhood Chain supports it, the request expresses it, while
    on Base it degrades gracefully to a standard WETH-paired launch.
    """
    parts = [f'deploy a token called "{req.name}" with symbol {req.symbol}']
    parts.append(f"on {req.chain}")
    if req.pair_with:
        parts.append(f"paired with {req.pair_with.upper()}")
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
