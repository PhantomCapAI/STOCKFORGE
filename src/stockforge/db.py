"""Async SQLite state persistence. One file, WAL mode, small surface.

Tables: signals, concepts, launches, fees, approvals, kv (counters/state).
This is deliberately schema-light — we store the full model as JSON plus a few
indexed columns, so the pipeline can evolve without migrations.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import aiosqlite

from .logging import get_logger
from .models import Approval, Concept, FeeSnapshot, LaunchRequest, LaunchResult, Signal

log = get_logger("db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY, ticker TEXT, score REAL, detected_at REAL, data TEXT
);
CREATE TABLE IF NOT EXISTS concepts (
    id TEXT PRIMARY KEY, symbol TEXT, paired_ticker TEXT, created_at REAL, data TEXT
);
CREATE TABLE IF NOT EXISTS launches (
    id TEXT PRIMARY KEY, request_id TEXT, status TEXT, token_address TEXT,
    chain TEXT, created_at REAL, data TEXT
);
CREATE TABLE IF NOT EXISTS fees (
    token_address TEXT, beneficiary TEXT, at REAL, data TEXT
);
CREATE TABLE IF NOT EXISTS claims (
    at REAL, treasury TEXT, ok INTEGER, mode TEXT, claimable_weth REAL, data TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY, kind TEXT, ref_id TEXT, status TEXT, created_at REAL, data TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS idx_launches_created ON launches(created_at);
CREATE INDEX IF NOT EXISTS idx_concepts_symbol ON concepts(symbol);
"""


class Store:
    def __init__(self, path: str):
        self.path = path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA foreign_keys=ON;")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        log.info("state store ready at %s", self.path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Store not connected; call connect() first")
        return self._db

    # ---- signals -------------------------------------------------------------
    async def save_signal(self, s: Signal) -> None:
        await self.db.execute(
            "INSERT OR REPLACE INTO signals VALUES (?,?,?,?,?)",
            (s.id, s.ticker, s.attention_score, s.detected_at, s.model_dump_json()),
        )
        await self.db.commit()

    # ---- concepts ------------------------------------------------------------
    async def save_concept(self, c: Concept) -> None:
        await self.db.execute(
            "INSERT OR REPLACE INTO concepts VALUES (?,?,?,?,?)",
            (c.id, c.symbol, c.paired_ticker, c.created_at, c.model_dump_json()),
        )
        await self.db.commit()

    async def recent_concept_slugs(self, limit: int = 500) -> list[str]:
        cur = await self.db.execute(
            "SELECT data FROM concepts ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        rows = await cur.fetchall()
        out = []
        for r in rows:
            try:
                out.append(Concept.model_validate_json(r["data"]).slug())
            except Exception:  # noqa: BLE001 - tolerant of legacy rows
                continue
        return out

    # ---- launches ------------------------------------------------------------
    async def save_launch(
        self, req: LaunchRequest, res: LaunchResult, record: dict[str, Any] | None = None
    ) -> None:
        # `record` is the secret-free structured launch record (observability.py).
        payload = {"request": req.model_dump(), "result": res.model_dump(), "record": record or {}}
        await self.db.execute(
            "INSERT OR REPLACE INTO launches VALUES (?,?,?,?,?,?,?)",
            (
                res.id,
                req.id,
                res.status.value,
                res.token_address,
                req.chain,
                req.created_at,
                json.dumps(payload),
            ),
        )
        await self.db.commit()

    async def launches_since(self, since_ts: float) -> list[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT data FROM launches WHERE created_at >= ? ORDER BY created_at DESC",
            (since_ts,),
        )
        rows = await cur.fetchall()
        return [json.loads(r["data"]) for r in rows]

    async def confirmed_token_addresses(self) -> list[str]:
        cur = await self.db.execute(
            "SELECT DISTINCT token_address FROM launches "
            "WHERE token_address != '' AND status IN ('confirmed','submitted')"
        )
        rows = await cur.fetchall()
        return [r["token_address"] for r in rows]

    # ---- fees ----------------------------------------------------------------
    async def save_fee_snapshot(self, f: FeeSnapshot) -> None:
        await self.db.execute(
            "INSERT INTO fees VALUES (?,?,?,?)",
            (f.token_address, f.beneficiary, f.at, f.model_dump_json()),
        )
        await self.db.commit()

    async def save_claim_record(self, record: dict[str, Any]) -> None:
        """Persist a secret-free fee-claim record (observability.build_claim_record)."""
        await self.db.execute(
            "INSERT INTO claims VALUES (?,?,?,?,?,?)",
            (
                record.get("timestamp"),
                record.get("treasury", ""),
                1 if record.get("ok") else 0,
                record.get("mode", ""),
                float(record.get("claimable_weth", 0.0)),
                json.dumps(record),
            ),
        )
        await self.db.commit()

    async def claim_summary(self) -> dict[str, Any]:
        """Local record of extraction activity: how many claims, how many
        succeeded, and the WETH those successful claims covered (as reported at
        claim time). Authoritative claimed totals come from Bankr's creator-fees
        endpoint; this is the agent's own audit trail."""
        cur = await self.db.execute(
            "SELECT COUNT(*), COALESCE(SUM(ok),0), "
            "COALESCE(SUM(CASE WHEN ok=1 THEN claimable_weth ELSE 0 END),0) FROM claims"
        )
        row = await cur.fetchone()
        total, ok_count, weth = (row[0], row[1], row[2]) if row else (0, 0, 0.0)
        cur2 = await self.db.execute(
            "SELECT at, mode, ok, claimable_weth FROM claims ORDER BY at DESC LIMIT 1"
        )
        last = await cur2.fetchone()
        return {
            "claim_attempts": int(total),
            "claim_successes": int(ok_count),
            "weth_claimed_recorded": float(weth),
            "last_claim": (
                {"at": last[0], "mode": last[1], "ok": bool(last[2]), "weth": last[3]}
                if last
                else None
            ),
        }

    # ---- approvals -----------------------------------------------------------
    async def save_approval(self, a: Approval) -> None:
        await self.db.execute(
            "INSERT OR REPLACE INTO approvals VALUES (?,?,?,?,?,?)",
            (a.id, a.kind.value, a.ref_id, a.status, a.created_at, a.model_dump_json()),
        )
        await self.db.commit()

    async def get_approval(self, approval_id: str) -> Approval | None:
        cur = await self.db.execute("SELECT data FROM approvals WHERE id=?", (approval_id,))
        row = await cur.fetchone()
        return Approval.model_validate_json(row["data"]) if row else None

    # ---- kv counters ---------------------------------------------------------
    async def kv_get(self, key: str, default: str = "") -> str:
        cur = await self.db.execute("SELECT v FROM kv WHERE k=?", (key,))
        row = await cur.fetchone()
        return row["v"] if row else default

    async def kv_set(self, key: str, value: str) -> None:
        await self.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, value))
        await self.db.commit()

    async def incr_daily_counter(self, name: str) -> int:
        """Increment a per-UTC-day counter and return the new value."""
        day = time.strftime("%Y-%m-%d", time.gmtime())
        key = f"counter:{name}:{day}"
        current = int(await self.kv_get(key, "0"))
        current += 1
        await self.kv_set(key, str(current))
        return current

    async def get_daily_counter(self, name: str) -> int:
        day = time.strftime("%Y-%m-%d", time.gmtime())
        return int(await self.kv_get(f"counter:{name}:{day}", "0"))
