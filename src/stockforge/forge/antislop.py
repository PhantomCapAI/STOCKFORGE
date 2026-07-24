"""Anti-slop + uniqueness checks for generated concepts.

Goal: keep the on-chain footprint clean. Reject low-effort, derivative, or
duplicate concepts before they ever reach the launcher. Returns a 0-1 score and
a list of reasons; the orchestrator gates on a minimum.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Overused meme-coin filler that signals low effort.
_SLOP_TOKENS = {
    "inu",
    "moon",
    "elon",
    "pepe",
    "wojak",
    "chad",
    "based",
    "gm",
    "wagmi",
    "safe",
    "baby",
    "mini",
    "doge",
    "shib",
    "2.0",
    "x",
    "ai",
}

_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")


@dataclass
class SlopVerdict:
    score: float  # 0-1, higher = cleaner/more unique
    ok: bool
    reasons: list[str] = field(default_factory=list)


class AntiSlop:
    def __init__(self, min_score: float = 0.6):
        self.min_score = min_score

    def check(
        self, name: str, symbol: str, thesis: str, recent_slugs: list[str] | None = None
    ) -> SlopVerdict:
        reasons: list[str] = []
        score = 1.0
        recent_slugs = recent_slugs or []

        # Symbol shape.
        if not _SYMBOL_RE.match(symbol):
            score -= 0.25
            reasons.append(f"symbol '{symbol}' not 2-10 uppercase alnum")

        # Slop tokens in the name.
        name_words = set(re.findall(r"[a-z0-9]+", name.lower()))
        slop_hits = name_words & _SLOP_TOKENS
        if slop_hits:
            score -= 0.15 * len(slop_hits)
            reasons.append(f"slop tokens in name: {sorted(slop_hits)}")

        # Thesis substance.
        words = thesis.split()
        if len(words) < 8:
            score -= 0.25
            reasons.append("thesis too thin (<8 words)")
        if len(set(w.lower() for w in words)) < max(1, len(words) * 0.55) and words:
            score -= 0.1
            reasons.append("thesis repetitive")

        # Duplicate vs recent launches.
        slug = f"{symbol}:{name}".lower()
        if slug in recent_slugs:
            score -= 0.6
            reasons.append("duplicate of a recent launch")
        for prev in recent_slugs:
            prev_sym = prev.split(":", 1)[0]
            if prev_sym and prev_sym == symbol.lower():
                score -= 0.2
                reasons.append(f"symbol collides with recent '{prev_sym}'")
                break

        score = max(0.0, min(1.0, score))
        return SlopVerdict(score=score, ok=score >= self.min_score, reasons=reasons)
