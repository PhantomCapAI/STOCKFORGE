"""Read creator fees via Bankr's public Doppler endpoints (verified, no auth).

  GET {base}/public/doppler/token-fees/{token}?days=30
  GET {base}/public/doppler/claimable-fees/{token}?beneficiary={addr}
  GET {base}/public/doppler/creator-fees/{addr}?days=30

Docs: docs.bankr.bot/token-launching/reading-fees
"""

from __future__ import annotations

import httpx

from ..logging import get_logger
from ..models import FeeSnapshot

log = get_logger("fees.reader")


def _to_float(v: object) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


class FeeReader:
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self._client = client
        self._owns = client is None

    async def __aenter__(self) -> FeeReader:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=20.0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns and self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=20.0)
        return self._client

    async def claimable_for(self, token_address: str, beneficiary: str) -> FeeSnapshot:
        """Single-address claimable lookup — cheap; use to gate a claim."""
        url = f"{self.base_url}/public/doppler/claimable-fees/{token_address}"
        snap = FeeSnapshot(token_address=token_address, beneficiary=beneficiary)
        try:
            r = await self.client.get(url, params={"beneficiary": beneficiary})
            r.raise_for_status()
            data = r.json()
        except Exception as e:  # noqa: BLE001
            log.warning("claimable_for(%s) failed: %s", token_address, e)
            return snap
        if not data.get("eligible"):
            return snap
        fees = data.get("claimableFees", {})
        snap.claimable_weth = _to_float(fees.get("token0"))
        snap.claimable_token = _to_float(fees.get("token1"))
        return snap

    async def token_fees(self, token_address: str, days: int = 30) -> FeeSnapshot:
        """Full per-token fee data incl. pool_id + initializer (needed for claims)."""
        url = f"{self.base_url}/public/doppler/token-fees/{token_address}"
        snap = FeeSnapshot(token_address=token_address, beneficiary="")
        try:
            r = await self.client.get(url, params={"days": min(days, 90)})
            r.raise_for_status()
            data = r.json()
        except Exception as e:  # noqa: BLE001
            log.warning("token_fees(%s) failed: %s", token_address, e)
            return snap
        tokens = data.get("tokens") or []
        if tokens:
            t = tokens[0]
            snap.pool_id = t.get("poolId", "")
            snap.initializer = t.get("initializer", "")
            snap.claimable_weth = _to_float(t.get("claimable", {}).get("token0"))
            snap.claimable_token = _to_float(t.get("claimable", {}).get("token1"))
            snap.claimed_weth = _to_float(t.get("claimed", {}).get("token0"))
        snap.lifetime_weth = _to_float(data.get("lifetimeEarnedWeth"))
        return snap

    async def creator_totals(self, beneficiary: str, days: int = 30) -> dict:
        """Aggregate fees across every token this address created."""
        url = f"{self.base_url}/public/doppler/creator-fees/{beneficiary}"
        try:
            r = await self.client.get(url, params={"days": min(days, 90)})
            r.raise_for_status()
            data = r.json()
        except Exception as e:  # noqa: BLE001
            log.warning("creator_totals(%s) failed: %s", beneficiary, e)
            return {}
        return {
            "lifetime_weth": _to_float(data.get("lifetimeEarnedWeth")),
            "claimable_weth": _to_float(data.get("totals", {}).get("claimableWeth")),
            "claimed_weth": _to_float(data.get("totals", {}).get("claimedWeth")),
            "token_count": len(data.get("tokens") or []),
        }
