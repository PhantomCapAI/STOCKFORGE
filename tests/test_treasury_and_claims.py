from stockforge.config import Settings
from stockforge.observability import build_claim_record, log_claim_record


def _s(**kw):
    return Settings(_env_file=None, **kw)


def test_treasury_defaults_to_beneficiary():
    s = _s(BANKR_BENEFICIARY_ADDRESS="0xBENE")
    assert s.treasury == "0xBENE"


def test_explicit_treasury_wins():
    s = _s(BANKR_BENEFICIARY_ADDRESS="0xBENE", STOCKFORGE_TREASURY_ADDRESS="0xTREASURY")
    assert s.treasury == "0xTREASURY"


def test_treasury_unset_is_empty():
    assert _s().treasury == ""


def test_autonomous_only_when_approval_off_and_not_dryrun():
    assert _s(STOCKFORGE_DRY_RUN=True, STOCKFORGE_REQUIRE_APPROVAL=False).autonomous is False
    assert _s(STOCKFORGE_DRY_RUN=False, STOCKFORGE_REQUIRE_APPROVAL=True).autonomous is False
    assert _s(STOCKFORGE_DRY_RUN=False, STOCKFORGE_REQUIRE_APPROVAL=False).autonomous is True


def test_auto_claim_defaults_true_and_threshold_default():
    s = _s()
    assert s.auto_claim is True
    assert s.fee_claim_min_weth == 0.001
    assert s.fee_sweep_every_ticks == 6


def test_redacted_includes_treasury_and_is_secret_free():
    red = _s(BANKR_API_KEY="bk_secret", STOCKFORGE_TREASURY_ADDRESS="0xTREASURY").redacted()
    assert red["treasury"] == "0xTREASURY"
    assert "auto_claim" in red and "autonomous" in red
    assert "bk_secret" not in str(red)


def test_claim_record_fields_and_secret_free():
    rec = build_claim_record(
        treasury="0xTREASURY",
        token_addresses=["0xtok1", "0xtok2"],
        claimable_weth=0.0123456789,
        dry_run=False,
        approval_status="auto (approval off)",
        mode="cli",
        ok=True,
        detail="claim broadcast",
        at=1_700_000_000.0,
    )
    for key in ("event", "timestamp", "treasury", "token_count", "claimable_weth", "dry_run", "mode", "ok"):
        assert key in rec
    assert rec["event"] == "fee_claim"
    assert rec["token_count"] == 2
    assert rec["claimable_weth"] == round(0.0123456789, 8)
    blob = str(rec)
    assert "bk_" not in blob and "PRIVATE" not in blob.upper()
    log_claim_record(rec)  # must not raise
