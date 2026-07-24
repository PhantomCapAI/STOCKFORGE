import json

from stockforge.config import Settings
from stockforge.models import LaunchRequest, LaunchResult, LaunchStatus, PairStatus
from stockforge.observability import build_claim_record, build_launch_record


def _s(tmp_path, **kw):
    base = dict(STOCKFORGE_DB_PATH=str(tmp_path / "e.sqlite"), _env_file=None)
    base.update(kw)
    return Settings(**base)


async def test_pair_confirmation_roundtrip(store):
    await store.confirm_pair("0xTOKEN", "NVDA", "checked pool on Bankr, quoted in NVDA")
    m = await store.pair_confirmed_map()
    assert m["0xTOKEN"]["confirmed"] is True
    assert m["0xTOKEN"]["ticker"] == "NVDA"
    assert "Bankr" in m["0xTOKEN"]["note"]


async def test_token_recipients_maps_wallet_and_ticker(store):
    req = LaunchRequest(
        concept_id="c", name="Silicon NVDA", symbol="SILNV", chain="robinhood",
        wallet_id="alpha", fee_recipient="0xALPHA", pair_with="NVDA",
    )
    res = LaunchResult(
        request_id=req.id, wallet_id="alpha", status=LaunchStatus.CONFIRMED,
        token_address="0xTOK", pair_status=PairStatus.REQUESTED, pair_requested="NVDA",
    )
    rec = build_launch_record(
        req, res, dry_run=False, approval_status="auto", backend="rest",
        prompt="p", paired_ticker="NVDA",
    )
    await store.save_launch(req, res, record=rec)
    tmap = await store.token_recipients()
    assert tmap["0xTOK"]["recipient"] == "0xALPHA"
    assert tmap["0xTOK"]["wallet_id"] == "alpha"
    assert tmap["0xTOK"]["ticker"] == "NVDA"


async def test_recent_claims_and_summary(store):
    for i in range(3):
        rec = build_claim_record(
            treasury="0xT", token_addresses=[f"0x{i}"], claimable_weth=0.01 * (i + 1),
            dry_run=False, approval_status="auto", mode="cli", ok=True, detail="ok",
            at=1_700_000_000.0 + i, wallet_id=f"w{i}",
        )
        await store.save_claim_record(rec)
    recent = await store.recent_claims(2)
    assert len(recent) == 2
    assert recent[0]["wallet_id"] == "w2"  # most recent first
    summary = await store.claim_summary()
    assert summary["claim_attempts"] == 3
    assert summary["claim_successes"] == 3


def test_claim_record_has_wallet_and_is_secret_free():
    rec = build_claim_record(
        treasury="0xRECIP", token_addresses=["0xa"], claimable_weth=0.5,
        dry_run=False, approval_status="auto", mode="cli", ok=True, detail="done",
        at=1_700_000_000.0, wallet_id="alpha",
    )
    assert rec["wallet_id"] == "alpha"
    assert rec["treasury"] == "0xRECIP"
    assert "PRIVATE" not in json.dumps(rec).upper() and "bk_" not in json.dumps(rec)
