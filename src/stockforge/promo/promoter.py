"""Post-launch promotion.

Phase 1 keeps promotion honest and dependency-free: it composes the launch
announcement and pushes it to the operator via Telegram (and logs it). Wiring a
real X/Twitter poster or the Phantom content engine is a drop-in: implement
`publish()` on a new channel and register it. We do NOT auto-post to public
social by default — that stays human-gated to keep the footprint clean.
"""

from __future__ import annotations

from ..logging import get_logger
from ..models import Concept, LaunchResult

log = get_logger("promo")


class Promoter:
    def __init__(self, notifier=None):
        # notifier: optional async callable(str) -> None (e.g. Telegram.send)
        self.notifier = notifier

    def compose_announcement(self, concept: Concept, result: LaunchResult) -> str:
        lines = [f"🚀 ${concept.symbol} — {concept.name}", ""]
        if result.token_address:
            lines.append(f"CA: {result.token_address}")
        if result.pool_url:
            lines.append(f"Pool: {result.pool_url}")
        lines.append("")
        lines.append(concept.launch_tweet or concept.thesis[:180])
        lines.append("")
        lines.append(f"Riding the {concept.paired_ticker} tape. Not affiliated with {concept.paired_ticker}. NFA.")
        return "\n".join(lines)

    async def promote(self, concept: Concept, result: LaunchResult) -> str:
        text = self.compose_announcement(concept, result)
        log.info("promo ready for $%s", concept.symbol)
        if self.notifier is not None:
            try:
                await self.notifier(f"📣 Promo draft:\n\n{text}")
            except Exception as e:  # noqa: BLE001
                log.warning("notifier failed: %s", e)
        return text
