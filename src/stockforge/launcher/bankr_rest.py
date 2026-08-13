"""Bankr Agent REST backend.

Verified surface (docs.bankr.bot/guides/zero-to-earning):
  POST {base}/agent/prompt
    headers: X-API-Key: bk_...
    body:    {"prompt": "deploy a token called X on base"}
  -> async job. The exact job-polling path is not fully specified in the public
     docs, so it is configurable (BANKR_AGENT_JOB_PATH); we try the documented
     shape and fall back gracefully. The launcher NEVER fabricates a token
     address — if the response doesn't contain one, status stays SUBMITTED and a
     human/next poll resolves it.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ..logging import get_logger
from ..models import LaunchRequest, LaunchResult, LaunchStatus
from .base import build_launch_prompt

log = get_logger("launcher.rest")

# Common places Bankr-style responses stash the deployed contract address.
_ADDRESS_KEYS = ("tokenAddress", "token_address", "address", "contractAddress")
_POOL_KEYS = ("poolId", "pool_id")
_INIT_KEYS = ("initializer", "feesManager")


def _dig(obj: Any, keys: tuple[str, ...]) -> str:
    """Best-effort recursive search for the first matching key with a str value."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k in keys and isinstance(v, str) and v:
                    return v
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return ""


class BankrRestBackend:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        job_path: str = "/agent/job/{job_id}",
        poll_interval: float = 3.0,
        poll_timeout: float = 120.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.job_path = job_path
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> BankrRestBackend:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise RuntimeError("BANKR_API_KEY is required for the REST backend")
        return {"X-API-Key": self.api_key, "Content-Type": "application/json"}

    async def launch(self, req: LaunchRequest) -> LaunchResult:
        prompt = build_launch_prompt(req)
        log.info("REST launch prompt: %s", prompt)
        try:
            resp = await self.client.post(
                f"{self.base_url}/agent/prompt",
                headers=self._headers(),
                json={"prompt": prompt},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500]
            return LaunchResult(
                request_id=req.id,
                status=LaunchStatus.FAILED,
                error=f"HTTP {e.response.status_code}: {body}",
            )
        except Exception as e:  # noqa: BLE001
            return LaunchResult(request_id=req.id, status=LaunchStatus.FAILED, error=str(e))

        job_id = _dig(data, ("jobId", "job_id", "id"))
        result = LaunchResult(
            request_id=req.id,
            status=LaunchStatus.SUBMITTED,
            job_id=job_id,
            raw=data if isinstance(data, dict) else {"data": data},
        )
        # If the prompt returned inline (synchronous) results, capture them.
        self._absorb(result, data)
        if job_id:
            await self._poll(job_id, result)
        return result

    async def _poll(self, job_id: str, result: LaunchResult) -> None:
        path = self.job_path.format(job_id=job_id)
        url = f"{self.base_url}{path}"
        deadline = self.poll_timeout
        waited = 0.0
        while waited < deadline:
            await asyncio.sleep(self.poll_interval)
            waited += self.poll_interval
            try:
                r = await self.client.get(url, headers=self._headers())
                if r.status_code == 404:
                    log.debug("job path %s not found; leaving as SUBMITTED", path)
                    return
                r.raise_for_status()
                data = r.json()
            except Exception as e:  # noqa: BLE001
                log.debug("poll error (non-fatal): %s", e)
                continue
            status = str(_dig(data, ("status", "state"))).lower()
            self._absorb(result, data)
            if result.token_address or status in ("completed", "success", "confirmed", "done"):
                result.status = (
                    LaunchStatus.CONFIRMED if result.token_address else LaunchStatus.SUBMITTED
                )
                return
            if status in ("failed", "error", "rejected"):
                result.status = LaunchStatus.FAILED
                result.error = _dig(data, ("error", "message")) or "job failed"
                return
        log.warning("job %s did not resolve within %.0fs; left SUBMITTED", job_id, deadline)

    @staticmethod
    def _absorb(result: LaunchResult, data: Any) -> None:
        addr = _dig(data, _ADDRESS_KEYS)
        if addr and not result.token_address:
            result.token_address = addr
        pool = _dig(data, _POOL_KEYS)
        if pool and not result.pool_id:
            result.pool_id = pool
        init = _dig(data, _INIT_KEYS)
        if init and not result.initializer:
            result.initializer = init
        tx = _dig(data, ("txHash", "tx_hash", "transactionHash"))
        if tx and not result.tx_hash:
            result.tx_hash = tx
        url = _dig(data, ("poolUrl", "pool_url", "dexUrl", "url"))
        if url and not result.pool_url:
            result.pool_url = url
