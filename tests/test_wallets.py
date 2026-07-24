import json

from stockforge.config import Settings
from stockforge.wallets import WalletPool


def _s(**kw):
    return Settings(_env_file=None, **kw)


def test_single_wallet_fallback_from_treasury():
    pool = WalletPool.from_settings(_s(BANKR_BENEFICIARY_ADDRESS="0xTREASURY"))
    assert len(pool.wallets) == 1
    assert pool.wallets[0].id == "main"
    assert pool.wallets[0].fee_recipient == "0xTREASURY"


def test_json_pool_parsing_and_fee_default():
    wallets = json.dumps(
        [
            {"id": "a", "fee_recipient": "0xA", "api_key": "bk_a"},
            {"id": "b"},  # fee_recipient defaults to treasury
        ]
    )
    pool = WalletPool.from_settings(_s(STOCKFORGE_WALLETS=wallets, BANKR_BENEFICIARY_ADDRESS="0xTREAS"))
    assert [w.id for w in pool.wallets] == ["a", "b"]
    assert pool.by_id("a").fee_recipient == "0xA"
    assert pool.by_id("b").fee_recipient == "0xTREAS"  # consolidates to treasury


def test_redacted_masks_keys():
    wallets = json.dumps([{"id": "a", "fee_recipient": "0xA", "api_key": "bk_secret", "private_key": "0xdead"}])
    pool = WalletPool.from_settings(_s(STOCKFORGE_WALLETS=wallets))
    red = pool.redacted()
    assert red[0]["api_key"] == "set" and red[0]["private_key"] == "set"
    assert "bk_secret" not in str(red) and "0xdead" not in str(red)


def test_bad_json_falls_back_to_single_wallet():
    pool = WalletPool.from_settings(_s(STOCKFORGE_WALLETS="{not json", BANKR_BENEFICIARY_ADDRESS="0xT"))
    assert len(pool.wallets) == 1 and pool.wallets[0].id == "main"


async def test_select_respects_global_budget(store):
    wallets = json.dumps([{"id": "a", "fee_recipient": "0xA"}, {"id": "b", "fee_recipient": "0xB"}])
    pool = WalletPool.from_settings(_s(STOCKFORGE_WALLETS=wallets))
    # Global budget 0 -> no wallet is selectable regardless of per-wallet room.
    assert await pool.select(store, global_budget=0, per_wallet_cap=50) is None
    # Budget available -> a wallet is returned.
    picked = await pool.select(store, global_budget=10, per_wallet_cap=50)
    assert picked is not None


async def test_select_distributes_least_recently_used(store):
    wallets = json.dumps([{"id": "a", "fee_recipient": "0xA"}, {"id": "b", "fee_recipient": "0xB"}])
    pool = WalletPool.from_settings(_s(STOCKFORGE_WALLETS=wallets))
    picked = await pool.select(store, global_budget=100, per_wallet_cap=50)
    assert picked is not None
    w1, rl1 = picked
    await pool.record_launch(store, w1, rl1)
    # Next selection should prefer the OTHER wallet (LRU distribution).
    picked2 = await pool.select(store, global_budget=100, per_wallet_cap=50)
    assert picked2 is not None
    assert picked2[0].id != w1.id
    # Global counter incremented once so far.
    assert await store.get_daily_counter("launch_all") == 1


async def test_per_wallet_cap_enforced(store):
    wallets = json.dumps([{"id": "a", "fee_recipient": "0xA"}])
    pool = WalletPool.from_settings(_s(STOCKFORGE_WALLETS=wallets))
    picked = await pool.select(store, global_budget=100, per_wallet_cap=1)
    w, rl = picked
    await pool.record_launch(store, w, rl)
    # Wallet 'a' hit its per-wallet cap of 1 -> no eligible wallet now.
    assert await pool.select(store, global_budget=100, per_wallet_cap=1) is None
