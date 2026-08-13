"""ImageForge — response parsing + enable gating (no network)."""

from __future__ import annotations

from stockforge.config import Settings
from stockforge.forge.image import ImageForge, parse_image_url


def _settings(**kw):
    base = dict(STOCKFORGE_DRY_RUN=True, _env_file=None)
    base.update(kw)
    return Settings(**base)


def test_parse_image_url_from_response():
    assert parse_image_url({"data": [{"url": "https://img/x.png"}]}) == "https://img/x.png"


def test_parse_image_url_empty_or_b64_is_safe():
    assert parse_image_url({}) == ""
    assert parse_image_url({"data": []}) == ""
    assert parse_image_url({"data": [{"b64_json": "abc"}]}) == ""


def test_disabled_without_key():
    f = ImageForge(_settings(STOCKFORGE_IMAGE_GEN=True))  # no XAI_API_KEY
    assert f.enabled is False


def test_disabled_when_flag_off():
    f = ImageForge(_settings(XAI_API_KEY="xai-test"))  # flag defaults off
    assert f.enabled is False


def test_enabled_with_key_and_flag():
    f = ImageForge(_settings(STOCKFORGE_IMAGE_GEN=True, XAI_API_KEY="xai-test"))
    assert f.enabled is True


async def test_generate_returns_empty_when_disabled():
    f = ImageForge(_settings())
    assert await f.generate("a prompt") == ""
