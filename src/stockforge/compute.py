"""Fees -> compute: the Bankr LLM Gateway funding seam.

This closes the official Bankr loop:

    Agent wallet -> launch token -> trading fees -> claim fees
                 -> PAY FOR COMPUTE (LLM Gateway) -> keep running

Verified surface (docs.bankr.bot/llm-gateway, help.bankr.bot/article/llm-gateway):
- Base URL: https://llm.bankr.bot (OpenAI-compatible at /v1/chat/completions),
  auth via X-API-Key: bk_... (or Authorization: Bearer). Beta-gated.
- LLM credits are USD, SEPARATE from the trading wallet. New accounts start at
  $0 and requests 402 until topped up.
- Credits are managed with the CLI:
    bankr llm credits                 # balance
    bankr llm credits add 25          # top up $25 (defaults to Base USDC)
    bankr llm credits auto --enable --amount 25 --threshold 5 --tokens USDC

This module is a clean foundation only — it does NOT autonomously buy credits
(that spends real money and its exact behavior must be verified by a human on a
funded account first). It reports how compute funding is wired so `status`,
`doctor`, and `preflight` can show the loop, and it centralizes the CLI commands
a human/automation would run to top up. Actual auto-topup stays a deliberate,
human-enabled step (`bankr llm credits auto`).
"""

from __future__ import annotations

from .config import Settings

# The exact, verified CLI commands for funding compute from wallet balance.
CREDITS_BALANCE_CMD = "bankr llm credits"
CREDITS_ADD_CMD = "bankr llm credits add 25"
CREDITS_AUTO_CMD = "bankr llm credits auto --enable --amount 25 --threshold 5 --tokens USDC"


def compute_funding_status(settings: Settings) -> dict:
    """Secret-free snapshot of how (and whether) fees can fund compute."""
    gateway_on = settings.llm_gateway_configured
    return {
        "loop": "fees -> claim -> LLM credits -> compute -> keep running",
        "llm_gateway": "configured" if gateway_on else "off (set FORGE_LLM_PROVIDER=bankr + BANKR_LLM_KEY/BANKR_API_KEY)",
        "gateway_base": settings.bankr_llm_base if gateway_on else "-",
        "model": settings.forge_llm_model if gateway_on else "-",
        # Bankr can also allocate a portion of launch fees to compute on its side;
        # that is configured in the Bankr dashboard, not here.
        "credits_note": "LLM credits are USD, separate from the trading wallet; top up on a funded account",
        "commands": {
            "balance": CREDITS_BALANCE_CMD,
            "top_up": CREDITS_ADD_CMD,
            "auto_top_up": CREDITS_AUTO_CMD,
        },
    }
