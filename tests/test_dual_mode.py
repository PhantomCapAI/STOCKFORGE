from stockforge.config import Settings
from stockforge.launcher.pairing import resolve_pair_with
from stockforge.models import Concept
from stockforge.orchestrator import Orchestrator
from stockforge.wallets import Wallet


def test_standard_mode_never_pairs():
    pair, note = resolve_pair_with("standard", "NVDA", "robinhood", is_stock=True)
    assert pair == "" and "forced" in note


def test_auto_pairs_stock_on_robinhood():
    pair, note = resolve_pair_with("auto", "nvda", "robinhood", is_stock=True)
    assert pair == "NVDA"


def test_pairing_needs_robinhood_chain():
    pair, note = resolve_pair_with("auto", "NVDA", "base", is_stock=True)
    assert pair == "" and "robinhood" in note


def test_non_stock_ticker_goes_standard():
    pair, note = resolve_pair_with("stock_paired", "ZZZ", "robinhood", is_stock=False)
    assert pair == "" and "not a recognized stock" in note


def test_launch_mode_config_default_and_lower():
    assert Settings(_env_file=None).launch_mode == "auto"
    assert Settings(STOCKFORGE_LAUNCH_MODE="STANDARD", _env_file=None).launch_mode == "standard"


def _orch(tmp_path, **kw):
    base = dict(STOCKFORGE_DB_PATH=str(tmp_path / "dm.sqlite"), STOCKFORGE_DEFAULT_CHAIN="robinhood", _env_file=None)
    base.update(kw)
    return Orchestrator(Settings(**base))


def _concept():
    return Concept(paired_ticker="NVDA", name="Silicon NVDA", symbol="SILNV", thesis="rides NVDA. Not affiliated with NVDA.")


def test_build_request_auto_pairs_watchlist_stock(tmp_path):
    orch = _orch(tmp_path)  # NVDA is in the default watchlist
    req = orch._build_request(_concept(), Wallet(id="main", fee_recipient="0xT"), mode="auto")
    assert req.pair_with == "NVDA"
    assert req.launch_mode == "auto"


def test_build_request_standard_mode_no_pair(tmp_path):
    orch = _orch(tmp_path)
    req = orch._build_request(_concept(), Wallet(id="main", fee_recipient="0xT"), mode="standard")
    assert req.pair_with == ""
    assert req.launch_mode == "standard"


def test_build_request_force_standard_degradation(tmp_path):
    orch = _orch(tmp_path)
    req = orch._build_request(_concept(), Wallet(id="main", fee_recipient="0xT"), mode="auto", force_standard=True)
    assert req.pair_with == ""  # degraded retry never pairs


def test_non_watchlist_ticker_not_stock(tmp_path):
    orch = _orch(tmp_path)
    assert orch._is_stock("NVDA") is True
    assert orch._is_stock("ZZZ") is False
