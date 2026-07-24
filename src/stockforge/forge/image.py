"""Token image generation via the xAI Imagine API.

Turns a concept's ``image_prompt`` into a hosted image URL that the launcher
injects into the Bankr launch (REST prompt `image <url>` / CLI `--image`). Best
effort: any failure returns "" and the launch proceeds without an image rather
than blocking. No key configured ⇒ disabled (returns "").
"""

from __future__ import annotations

import httpx

from ..config import Settings
from ..logging import get_logger

log = get_logger("forge.image")


class ImageForge:
    """Generates a token image from a text prompt. Currently backed by xAI
    (`POST /v1/images/generations`); the shape is OpenAI-compatible so any
    equivalent endpoint works by pointing XAI_BASE_URL/model elsewhere."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: httpx.AsyncClient | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.settings.image_gen_enabled and self.settings.xai_api_key)

    async def generate(self, prompt: str) -> str:
        """Return a hosted image URL for the prompt, or "" on any problem."""
        if not self.enabled or not prompt.strip():
            return ""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0)
        url = f"{self.settings.xai_base_url.rstrip('/')}/images/generations"
        headers = {"Authorization": f"Bearer {self.settings.xai_api_key}"}
        body = {
            "model": self.settings.xai_image_model,
            "prompt": prompt[:1024],
            "n": 1,
            "response_format": "url",
        }
        try:
            r = await self._client.post(url, headers=headers, json=body)
            r.raise_for_status()
            return parse_image_url(r.json())
        except Exception as e:  # noqa: BLE001
            log.warning("image generation failed (%s); launching without image", e)
            return ""

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def parse_image_url(payload: dict) -> str:
    """Extract the first image URL from an xAI/OpenAI-style images response.
    Tolerant of url vs b64 (returns "" for b64 — we only inject hosted URLs)."""
    data = payload.get("data") or []
    if not data:
        return ""
    first = data[0] or {}
    return str(first.get("url") or "")
