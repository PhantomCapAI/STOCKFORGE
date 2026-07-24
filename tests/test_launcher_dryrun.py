from stockforge.launcher import BankrLauncher
from stockforge.launcher.base import build_cli_args, build_launch_prompt
from stockforge.models import LaunchRequest, LaunchStatus


def _req(**kw):
    base = dict(concept_id="c1", name="Silicon NVDA", symbol="SILNV", chain="base")
    base.update(kw)
    return LaunchRequest(**base)


async def test_rest_dry_run_never_broadcasts(settings):
    settings.dry_run = True
    settings.bankr_backend = "rest"
    launcher = BankrLauncher(settings)
    res = await launcher.launch(_req())
    assert res.status is LaunchStatus.SIMULATED
    assert res.token_address == ""


def test_prompt_includes_pairing_on_robinhood():
    req = _req(chain="robinhood", pair_with="NVDA")
    prompt = build_launch_prompt(req)
    assert "robinhood" in prompt
    assert "paired with NVDA" in prompt


def test_cli_args_simulate_flag():
    args = build_cli_args(_req(), simulate=True)
    assert "--simulate" in args
    assert "--yes" not in args
    args2 = build_cli_args(_req(), simulate=False)
    assert "--yes" in args2
    assert "--simulate" not in args2


def test_cli_args_include_chain_and_symbol():
    args = build_cli_args(_req(chain="robinhood"), simulate=False)
    assert "--chain" in args and "robinhood" in args
    assert "--symbol" in args and "SILNV" in args


def test_preview_shape(settings):
    launcher = BankrLauncher(settings)
    p = launcher.preview(_req(pair_with=""))
    assert p["symbol"] == "SILNV"
    assert p["dry_run"] is settings.dry_run
    assert "prompt" in p
