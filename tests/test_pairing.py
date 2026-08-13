from stockforge.launcher.bankr import BankrLauncher
from stockforge.launcher.base import build_launch_prompt
from stockforge.launcher.pairing import (
    CLI_SUPPORTS_STOCK_PAIR,
    classify_pairing,
    find_quote_labels,
    stock_pair_phrase,
)
from stockforge.models import LaunchRequest, LaunchStatus, PairStatus


def _req(**kw):
    base = dict(concept_id="c1", name="Silicon NVDA", symbol="SILNV", chain="robinhood", pair_with="NVDA")
    base.update(kw)
    return LaunchRequest(**base)


def test_phrase_robinhood_vs_base():
    assert stock_pair_phrase("nvda", "robinhood") == "paired with NVDA on robinhood chain"
    assert stock_pair_phrase("nvda", "base") == "on base"


def test_prompt_expresses_pair_and_chain_together():
    p = build_launch_prompt(_req())
    assert "paired with NVDA on robinhood chain" in p


def test_prompt_no_pair_on_base():
    p = build_launch_prompt(_req(chain="base", pair_with=""))
    assert "on base" in p
    assert "paired with" not in p


def test_find_quote_labels_digs_nested():
    raw = {"pool": {"token0Label": "WETH", "token1Label": "SILNV"}, "other": [{"quoteSymbol": "NVDA"}]}
    labels = find_quote_labels(raw)
    assert "WETH" in labels and "NVDA" in labels and "SILNV" in labels


def test_classify_not_requested():
    assert classify_pairing("", LaunchStatus.CONFIRMED, "0xabc", []) is PairStatus.NOT_REQUESTED


def test_classify_accepted_when_stock_in_labels():
    assert (
        classify_pairing("NVDA", LaunchStatus.CONFIRMED, "0xabc", ["NVDA", "SILNV"])
        is PairStatus.ACCEPTED
    )


def test_classify_degraded_when_only_standard_quote():
    assert (
        classify_pairing("NVDA", LaunchStatus.CONFIRMED, "0xabc", ["WETH", "SILNV"])
        is PairStatus.DEGRADED
    )


def test_classify_rejected_on_failure():
    assert classify_pairing("NVDA", LaunchStatus.FAILED, "", []) is PairStatus.REJECTED


def test_classify_requested_when_no_evidence():
    # dry-run / simulated with no labels -> requested (unknown), not a false claim
    assert classify_pairing("NVDA", LaunchStatus.SIMULATED, "", []) is PairStatus.REQUESTED


async def test_dryrun_launch_sets_pair_status(settings):
    settings.dry_run = True
    settings.bankr_backend = "rest"
    res = await BankrLauncher(settings).launch(_req())
    assert res.status is LaunchStatus.SIMULATED
    assert res.pair_requested == "NVDA"
    assert res.pair_status is PairStatus.REQUESTED  # can't confirm in dry-run — honest


def test_cli_pairing_is_not_faked():
    # We must not invent a CLI pairing flag until Bankr documents one.
    assert CLI_SUPPORTS_STOCK_PAIR is False
