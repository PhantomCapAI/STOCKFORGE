from stockforge.config import Settings


def test_defaults_are_safe():
    s = Settings(_env_file=None)
    assert s.dry_run is True
    assert s.require_approval is True


def test_watchlist_parsing():
    s = Settings(STOCKFORGE_WATCHLIST="nvda, gme ,tsla", _env_file=None)
    assert s.watchlist == ["NVDA", "GME", "TSLA"]


def test_redacted_masks_secrets():
    s = Settings(BANKR_API_KEY="bk_secret", BANKR_PRIVATE_KEY="0xdead", _env_file=None)
    red = s.redacted()
    assert red["bankr_api_key"] == "set"
    assert red["bankr_private_key"] == "set"
    assert "bk_secret" not in str(red)
    assert "0xdead" not in str(red)


def test_telegram_enabled_requires_both():
    assert Settings(TELEGRAM_BOT_TOKEN="t", _env_file=None).telegram_enabled is False
    assert Settings(TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID="1", _env_file=None).telegram_enabled
