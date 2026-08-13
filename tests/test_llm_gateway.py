from stockforge.compute import compute_funding_status
from stockforge.config import Settings
from stockforge.forge import ConceptForge


def _s(**kw):
    return Settings(_env_file=None, **kw)


def test_gateway_off_by_default():
    s = _s()
    assert s.forge_llm_provider == "none"
    assert s.llm_gateway_configured is False
    base, key, model, auth = s.forge_effective()
    assert auth == "bearer"  # generic default


def test_bankr_provider_uses_gateway_and_xapikey():
    s = _s(FORGE_LLM_PROVIDER="bankr", BANKR_API_KEY="bk_test")
    assert s.llm_gateway_key == "bk_test"  # falls back to API key
    assert s.llm_gateway_configured is True
    base, key, model, auth = s.forge_effective()
    assert base == "https://llm.bankr.bot/v1"
    assert key == "bk_test"
    assert auth == "x-api-key"


def test_dedicated_llm_key_wins():
    s = _s(FORGE_LLM_PROVIDER="bankr", BANKR_API_KEY="bk_api", BANKR_LLM_KEY="bk_llm")
    assert s.llm_gateway_key == "bk_llm"


def test_bankr_provider_without_key_not_configured():
    s = _s(FORGE_LLM_PROVIDER="bankr")  # no key anywhere
    assert s.llm_gateway_configured is False


def test_forge_llm_enabled_gating():
    # bankr provider with a key -> LLM enabled; without -> template only
    on = ConceptForge(_s(FORGE_LLM_PROVIDER="bankr", BANKR_API_KEY="bk_x"))
    off = ConceptForge(_s(FORGE_LLM_PROVIDER="bankr"))
    assert on._llm_enabled() is True
    assert off._llm_enabled() is False


def test_compute_status_is_secret_free_and_has_commands():
    s = _s(FORGE_LLM_PROVIDER="bankr", BANKR_API_KEY="bk_secret")
    st = compute_funding_status(s)
    assert st["llm_gateway"] == "configured"
    assert "bankr llm credits add" in st["commands"]["top_up"]
    assert "bk_secret" not in str(st)


def test_redacted_reports_gateway_and_masks_llm_key():
    red = _s(FORGE_LLM_PROVIDER="bankr", BANKR_LLM_KEY="bk_secret").redacted()
    assert red["llm_gateway"] == "configured"
    assert red["bankr_llm_key"] == "set"
    assert "bk_secret" not in str(red)
