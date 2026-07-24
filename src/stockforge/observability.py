"""Structured, secret-free launch records.

Every launch attempt — dry-run included — produces one structured record that is
both logged (one JSON line to stdout, so Zeabur/Docker capture it) and persisted.
Records never contain API keys, private keys, or tokens: the only address-like
value included is the public fee-recipient / beneficiary, which is not a secret.
"""

from __future__ import annotations

import json
import time
from typing import Any

from .logging import get_logger
from .models import LaunchRequest, LaunchResult, PairStatus

log = get_logger("observability")


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts))


def final_mode(pair_status: PairStatus) -> str:
    """Human-readable 'what pool did we actually end up on' label."""
    return {
        PairStatus.ACCEPTED: "stock-pair",
        PairStatus.REQUESTED: "stock-pair (requested, unverified)",
        PairStatus.DEGRADED: "standard (pair degraded to WETH)",
        PairStatus.REJECTED: "standard (pair rejected / launch failed)",
        PairStatus.NOT_REQUESTED: "standard",
    }.get(pair_status, "standard")


def build_launch_record(
    req: LaunchRequest,
    result: LaunchResult,
    *,
    dry_run: bool,
    approval_status: str,
    backend: str,
    prompt: str,
    paired_ticker: str = "",
    cli_command: list[str] | None = None,
) -> dict[str, Any]:
    """Assemble the secret-free structured record for one launch attempt."""
    bankr_summary = {
        "status": result.status.value,
        "token_address": result.token_address,
        "job_id": result.job_id,
        "pool_url": result.pool_url,
        "error": (result.error or "")[:300],
    }
    record: dict[str, Any] = {
        "event": "launch_attempt",
        "timestamp": _iso(result.finished_at),
        "wallet_id": req.wallet_id,
        "name": req.name,
        "ticker": req.symbol,
        "paired_ticker": paired_ticker or req.pair_with,
        "launch_mode": req.launch_mode,  # requested mode: auto/stock_paired/standard
        "requested_pair": req.pair_with or "",
        "final_mode": final_mode(result.pair_status),
        "pair_status": result.pair_status.value,
        "quote_labels": result.quote_labels,
        "chain": req.chain,
        "backend": backend,
        "dry_run": dry_run,
        "approval_status": approval_status,
        "status": result.status.value,
        "bankr_summary": bankr_summary,
        "prompt": prompt,  # exact NL prompt sent to Bankr (no secrets)
    }
    if cli_command is not None:
        record["cli_command"] = cli_command  # exact argv (auth is via env, never argv)
    return record


def log_launch_record(record: dict[str, Any]) -> None:
    """Emit the record as a single JSON line for log scrapers."""
    log.info("launch_record %s", json.dumps(record, separators=(",", ":"), sort_keys=True))


def build_claim_record(
    *,
    treasury: str,
    token_addresses: list[str],
    claimable_weth: float,
    dry_run: bool,
    approval_status: str,
    mode: str,
    ok: bool,
    detail: str,
    at: float,
    wallet_id: str = "main",
) -> dict[str, Any]:
    """Secret-free record of a fee-claim attempt. `treasury` is the public fee
    recipient — never a private key. `mode` is cli / build-claim / dry-run."""
    return {
        "event": "fee_claim",
        "timestamp": _iso(at),
        "wallet_id": wallet_id,
        "treasury": treasury,
        "token_count": len(token_addresses),
        "tokens": token_addresses[:50],
        "claimable_weth": round(claimable_weth, 8),
        "dry_run": dry_run,
        "approval_status": approval_status,
        "mode": mode,
        "ok": ok,
        "detail": (detail or "")[:300],
    }


def log_claim_record(record: dict[str, Any]) -> None:
    """Emit the fee-claim record as a single JSON line for log scrapers."""
    log.info("claim_record %s", json.dumps(record, separators=(",", ":"), sort_keys=True))
