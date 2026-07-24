"""Concept generation.

Two modes:
  * provider="none"  -> deterministic template forge (no external calls, always
    available, good for dry-runs and CI).
  * provider="openai_compatible" -> calls an OpenAI-style chat endpoint with the
    prompt in prompts/concept_generation.md and parses strict JSON.

Either way the result is validated by AntiSlop before it can be launched.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from ..config import Settings
from ..logging import get_logger
from ..models import Concept, Signal
from .antislop import AntiSlop

log = get_logger("forge.concept")

_PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "concept_generation.md"

# Deterministic word banks for the template forge.
_ARCHETYPES = {
    "NVDA": ("Silicon", "compute demand and AI infrastructure buildout"),
    "GME": ("Diamond", "retail conviction and short-interest reflexivity"),
    "TSLA": ("Voltage", "EV cycle and autonomy optionality"),
    "HOOD": ("Payflow", "retail brokerage volume and tokenized equities"),
    "SPY": ("Broadtape", "macro beta and index-flow momentum"),
    "AMD": ("Lattice", "accelerator share gains"),
    "PLTR": ("Ontology", "government and enterprise AI deployment"),
    "MSTR": ("Treasury", "leveraged digital-asset balance sheet"),
}


class ConceptForge:
    def __init__(self, settings: Settings, antislop: AntiSlop | None = None):
        self.settings = settings
        self.antislop = antislop or AntiSlop()
        self._client: httpx.AsyncClient | None = None

    def _llm_enabled(self) -> bool:
        """True when a real LLM provider is configured with a usable key."""
        provider = self.settings.forge_llm_provider
        if provider == "openai_compatible":
            return bool(self.settings.forge_llm_api_key)
        if provider == "bankr":  # fees -> compute via the Bankr LLM Gateway
            return bool(self.settings.llm_gateway_key)
        return False

    async def forge(self, signal: Signal, recent_slugs: list[str] | None = None) -> Concept | None:
        if self._llm_enabled():
            concept = await self._forge_llm(signal)
        else:
            concept = self._forge_template(signal)
        if concept is None:
            return None
        verdict = self.antislop.check(
            concept.name, concept.symbol, concept.thesis, recent_slugs=recent_slugs
        )
        concept.uniqueness_score = verdict.score
        if not verdict.ok:
            log.info("concept '%s' rejected by anti-slop: %s", concept.symbol, verdict.reasons)
            return None
        return concept

    # ---- template forge ------------------------------------------------------
    def _forge_template(self, signal: Signal) -> Concept:
        ticker = signal.ticker.upper()
        root, driver = _ARCHETYPES.get(ticker, (ticker.title(), "market attention"))
        # Symbol derived from ticker + short salt from the signal id for uniqueness.
        salt = signal.id[:3].upper()
        symbol = f"{ticker[:3]}{salt}"[:8]
        name = f"{root} {ticker}"
        thesis = (
            f"{name} rides the {ticker} narrative driven by {driver}. "
            f"It exists to capture attention flow around {ticker} moves and route "
            f"trading-fee revenue back into the agent's compute. Not affiliated with {ticker}."
        )
        return Concept(
            signal_id=signal.id,
            paired_ticker=ticker,
            name=name,
            symbol=symbol,
            thesis=thesis,
            image_prompt=(
                f"Bold minimalist emblem for '{name}', motif evoking {root.lower()} and "
                f"{ticker} energy, high-contrast, vector, no text, crypto-native."
            ),
            launch_tweet=(
                f"${symbol} is live — riding the {ticker} tape. "
                f"Attention in, fees out. Not affiliated with {ticker}. NFA."
            ),
        )

    # ---- LLM forge -----------------------------------------------------------
    async def _forge_llm(self, signal: Signal) -> Concept | None:
        prompt = self._load_prompt()
        user = (
            f"Ticker: {signal.ticker}\nHeadline: {signal.headline}\n"
            f"Attention score: {signal.attention_score:.0f}/100\n"
            f"Sources: {', '.join(signal.sources)}\n\n"
            "Return ONLY the JSON object."
        )
        base_url, api_key, model, auth_style = self.settings.forge_effective()
        headers = (
            {"X-API-Key": api_key}
            if auth_style == "x-api-key"
            else {"Authorization": f"Bearer {api_key}"}
        )
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0)
        try:
            r = await self._client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.9,
                    "response_format": {"type": "json_object"},
                },
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
        except Exception as e:  # noqa: BLE001
            log.warning("LLM forge failed (%s); falling back to template", e)
            return self._forge_template(signal)
        try:
            return Concept(
                signal_id=signal.id,
                paired_ticker=signal.ticker.upper(),
                name=data["name"],
                symbol=str(data["symbol"]).upper().lstrip("$"),
                thesis=data["thesis"],
                image_prompt=data.get("image_prompt", ""),
                launch_tweet=data.get("launch_tweet", ""),
            )
        except (KeyError, TypeError) as e:
            log.warning("LLM output missing fields (%s); using template", e)
            return self._forge_template(signal)

    def _load_prompt(self) -> str:
        try:
            return _PROMPT_PATH.read_text(encoding="utf-8")
        except OSError:
            return (
                "You are a crypto token concept designer. Given a stock narrative, "
                "output JSON with keys: name, symbol, thesis, image_prompt, launch_tweet."
            )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
