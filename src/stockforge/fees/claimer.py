"""Claim creator fees.

Two paths, both verified:
  1. CLI wallet claim (headless):  `bankr --ni fees claim-wallet --all`
     — signs + broadcasts using BANKR_PRIVATE_KEY. This moves real value, so it
     only runs when dry_run=False AND a private key is present.
  2. build-claim (no key on our side):
     POST {base}/public/doppler/build-claim {beneficiaryAddress, tokenAddresses[]}
     — returns UNSIGNED transactions for a human/wallet to sign. Safe default.

Docs: docs.bankr.bot/token-launching/reading-fees + /cli
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from ..config import Settings
from ..logging import get_logger

log = get_logger("fees.claimer")


@dataclass
class ClaimOutcome:
    ok: bool
    mode: str  # "cli" | "build-claim" | "dry-run"
    detail: str = ""
    unsigned_txs: list | None = None
    stdout: str = ""


class FeeClaimer:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._client = client

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def build_unsigned(
        self, token_addresses: list[str], beneficiary: str | None = None
    ) -> ClaimOutcome:
        """Build unsigned claim txs (no private key required). Up to 50 tokens.
        Claims to `beneficiary` (the fee recipient for these tokens); defaults to
        the treasury."""
        beneficiary = beneficiary or self.settings.treasury
        if not beneficiary:
            return ClaimOutcome(False, "build-claim", "no treasury/beneficiary set")
        url = f"{self.settings.bankr_api_base}/public/doppler/build-claim"
        try:
            r = await self.client.post(
                url,
                json={
                    "beneficiaryAddress": beneficiary,
                    "tokenAddresses": token_addresses[:50],
                },
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:  # noqa: BLE001
            return ClaimOutcome(False, "build-claim", f"request failed: {e}")
        txs = data.get("transactions") or data.get("txs") or data
        return ClaimOutcome(True, "build-claim", "unsigned txs ready", unsigned_txs=txs)

    async def claim_wallet_cli(self, private_key: str | None = None) -> ClaimOutcome:
        """Headless claim-all via the CLI. Uses `private_key` (a specific wallet's
        hot key) or falls back to BANKR_PRIVATE_KEY. Requires a key + not dry_run.
        The key is passed via env, never on the command line."""
        if self.settings.dry_run:
            return ClaimOutcome(True, "dry-run", "dry_run=True; claim suppressed")
        key = private_key or self.settings.bankr_private_key
        if not key:
            return ClaimOutcome(
                False, "cli", "no private key for this wallet; use build_unsigned() instead"
            )
        env_note = "private key passed via env (never argv)"
        args = [self.settings.bankr_cli_bin, "--ni", "fees", "claim-wallet", "--all"]
        log.info("CLI claim-all (%s)", env_note)
        try:
            import os

            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "BANKR_PRIVATE_KEY": key},
            )
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=180.0)
        except FileNotFoundError:
            return ClaimOutcome(False, "cli", "bankr CLI not found (`npm i -g @bankr/cli`)")
        except TimeoutError:
            return ClaimOutcome(False, "cli", "claim timed out")
        out = out_b.decode(errors="replace")
        err = err_b.decode(errors="replace")
        if proc.returncode != 0:
            return ClaimOutcome(False, "cli", (err or out)[:500], stdout=out)
        return ClaimOutcome(True, "cli", "claim broadcast", stdout=out[:1000])

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
