from stockforge.models import LaunchRequest, LaunchResult, LaunchStatus, PairStatus
from stockforge.observability import build_launch_record, final_mode, log_launch_record


def _req(**kw):
    base = dict(concept_id="c1", name="Silicon NVDA", symbol="SILNV", chain="robinhood", pair_with="NVDA")
    base.update(kw)
    return LaunchRequest(**base)


def test_final_mode_labels():
    assert final_mode(PairStatus.ACCEPTED) == "stock-pair"
    assert final_mode(PairStatus.NOT_REQUESTED) == "standard"
    assert "requested" in final_mode(PairStatus.REQUESTED)
    assert "degraded" in final_mode(PairStatus.DEGRADED).lower()


def test_record_has_all_required_fields():
    req = _req()
    res = LaunchResult(request_id=req.id, status=LaunchStatus.SIMULATED, pair_status=PairStatus.REQUESTED, pair_requested="NVDA")
    rec = build_launch_record(
        req, res, dry_run=True, approval_status="not_required (dry-run)",
        backend="rest", prompt="deploy a token called ... paired with NVDA on robinhood chain",
        paired_ticker="NVDA",
    )
    for key in (
        "timestamp", "name", "ticker", "requested_pair", "final_mode",
        "dry_run", "approval_status", "status", "bankr_summary", "prompt",
    ):
        assert key in rec, f"missing {key}"
    assert rec["dry_run"] is True
    assert rec["requested_pair"] == "NVDA"
    assert rec["final_mode"].startswith("stock-pair")


def test_record_is_secret_free():
    # The record must never carry api keys / private keys — only public data.
    req = _req(fee_recipient="0xBeneficiaryPublicAddress")
    res = LaunchResult(request_id=req.id, status=LaunchStatus.SIMULATED)
    rec = build_launch_record(
        req, res, dry_run=True, approval_status="x", backend="rest",
        prompt="deploy ...", paired_ticker="NVDA",
    )
    blob = str(rec)
    assert "bk_" not in blob  # no bankr api key prefix
    assert "0x" not in blob or "0xBeneficiary" in blob  # only the public recipient, no private key
    # sanity: logging it does not raise
    log_launch_record(rec)


def test_cli_command_recorded_only_for_cli_backend():
    req = _req()
    res = LaunchResult(request_id=req.id, status=LaunchStatus.SIMULATED)
    rec_rest = build_launch_record(req, res, dry_run=True, approval_status="x", backend="rest", prompt="p")
    assert "cli_command" not in rec_rest
    rec_cli = build_launch_record(req, res, dry_run=True, approval_status="x", backend="cli", prompt="p", cli_command=["launch", "--name", "Silicon NVDA"])
    assert rec_cli["cli_command"][0] == "launch"
