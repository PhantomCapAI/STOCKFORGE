from stockforge.config import Settings
from stockforge.models import LaunchStatus, Signal
from stockforge.orchestrator import Orchestrator
from stockforge.signal import AttentionScorer


def _settings(tmp_path, **kw):
    base = dict(
        STOCKFORGE_DB_PATH=str(tmp_path / "c.sqlite"),
        STOCKFORGE_DRY_RUN=True,
        STOCKFORGE_REQUIRE_APPROVAL=False,  # autonomous-style, but dry-run keeps it safe
        BANKR_BACKEND="rest",
        STOCKFORGE_DAILY_LAUNCH_BUDGET=3,
        BANKR_BENEFICIARY_ADDRESS="0xTREASURY",
        _env_file=None,
    )
    base.update(kw)
    return Settings(**base)


async def _concept(orch):
    sig = AttentionScorer().enrich(
        Signal(ticker="NVDA", headline="NVDA squeeze rally record", sources=["a", "b", "c"], meta={"magnitude": 15})
    )
    return await orch.forge.forge(sig, recent_slugs=[])


async def test_autonomous_dryrun_launch_records_and_simulates(tmp_path):
    orch = Orchestrator(_settings(tmp_path))
    await orch.store.connect()
    try:
        concept = await _concept(orch)
        assert concept is not None
        result = await orch.gated_launch(concept)
        assert result is not None
        assert result.status is LaunchStatus.SIMULATED  # dry-run never broadcasts
        # Fee recipient routed to treasury.
        # Persisted launch record present with dry_run flag + approval status.
        rows = await orch.store.launches_since(0)
        assert rows and rows[0]["record"]["dry_run"] is True
        assert rows[0]["record"]["approval_status"].startswith("not_required")
    finally:
        await orch.shutdown()


async def test_budget_consumed_and_gate_closes(tmp_path):
    orch = Orchestrator(_settings(tmp_path, STOCKFORGE_DAILY_LAUNCH_BUDGET=1))
    await orch.store.connect()
    try:
        concept = await _concept(orch)
        first = await orch.gated_launch(concept)
        assert first is not None  # first launch goes through (dry-run simulated)
        # Budget now exhausted (1/day) -> next attempt is gated, returns None.
        second = await orch.gated_launch(concept)
        assert second is None
        assert await orch.rate.remaining_today() == 0
    finally:
        await orch.shutdown()


async def test_dryrun_claim_writes_secret_free_record(tmp_path):
    orch = Orchestrator(_settings(tmp_path))
    await orch.store.connect()
    try:
        await orch._maybe_claim("0xTREASURY", ["0xtok1", "0xtok2"], 0.05)
        # A claim record row is persisted even in dry-run (transparency).
        cur = await orch.store.db.execute("SELECT data FROM claims")
        rows = await cur.fetchall()
        assert rows, "expected a persisted claim record"
        assert "PRIVATE" not in rows[0]["data"].upper()
        import json
        rec = json.loads(rows[0]["data"])
        assert rec["treasury"] == "0xTREASURY"  # recipient recorded per claim
    finally:
        await orch.shutdown()
