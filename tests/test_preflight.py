from stockforge.config import Settings
from stockforge.health import run_preflight


def _settings(tmp_path, **kw):
    base = dict(STOCKFORGE_DB_PATH=str(tmp_path / "pf.sqlite"), _env_file=None)
    base.update(kw)
    return Settings(**base)


async def test_preflight_not_ready_when_unconfigured(tmp_path):
    s = _settings(tmp_path)  # nothing configured, dry-run on
    checks, ready = await run_preflight(s)
    assert ready is False  # missing critical env for live
    names = [c.name for c in checks]
    assert "dry-run currently ON" in names
    assert "stock-pairing status" in names
    assert "critical env vars" in names


async def test_preflight_flags_stock_pairing_unverified(tmp_path):
    s = _settings(tmp_path)
    checks, _ = await run_preflight(s)
    pairing = next(c for c in checks if c.name == "stock-pairing status")
    assert "UNVERIFIED" in pairing.message
    assert pairing.level == "warn"


async def test_preflight_lists_missing_env(tmp_path):
    s = _settings(tmp_path, BANKR_BACKEND="rest")
    checks, ready = await run_preflight(s)
    env_check = next(c for c in checks if c.name == "critical env vars")
    assert "BANKR_API_KEY" in env_check.message
    assert "BANKR_BENEFICIARY_ADDRESS" in env_check.message
    assert ready is False


async def test_preflight_dryrun_line_reflects_flag(tmp_path):
    s = _settings(tmp_path, STOCKFORGE_DRY_RUN=True)
    checks, _ = await run_preflight(s)
    dr = next(c for c in checks if c.name == "dry-run currently ON")
    assert dr.level == "ok"
