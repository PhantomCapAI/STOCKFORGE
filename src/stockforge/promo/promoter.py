"""Post-launch promotion — minimum viable, honest, operator-gated.

On each launch we auto-generate a small promo kit (launch tweet, a one-line
narrative hook, and the launch link) and hand it to the operator via Telegram +
logs. This gives a launch a chance at real trading volume without building a
social empire.

We do NOT auto-post to public social. Public posting stays human-gated on
purpose — a `Publisher` seam is provided so a real X poster / content agent can
be plugged in later, but the default publishers only notify the operator. The
"not affiliated with <TICKER>" disclaimer is always included.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..logging import get_logger
from ..models import Concept, LaunchResult

log = get_logger("promo")

_TWEET_MAX = 270


@dataclass
class PromoKit:
    """Everything needed to promote one launch. Copy only — nothing is posted."""

    symbol: str
    name: str
    one_liner: str
    tweet: str
    launch_link: str = ""
    hashtags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "one_liner": self.one_liner,
            "tweet": self.tweet,
            "launch_link": self.launch_link,
            "hashtags": self.hashtags,
        }

    def render(self) -> str:
        lines = [f"📣 ${self.symbol} — {self.name}", "", self.one_liner, "", self.tweet]
        if self.launch_link:
            lines += ["", f"🔗 {self.launch_link}"]
        return "\n".join(lines)


class Publisher(Protocol):
    """A promo channel. The default ones only notify the operator; a real X /
    content-agent publisher implements this later without touching the loop."""

    name: str

    async def publish(self, kit: PromoKit) -> None: ...


class OperatorNotifyPublisher:
    """Sends the promo kit to the operator (Telegram). Human-gated — the operator
    decides whether to post it publicly. This is the only default publisher."""

    name = "operator-notify"

    def __init__(self, notifier):
        self.notifier = notifier  # async callable(str) -> None

    async def publish(self, kit: PromoKit) -> None:
        if self.notifier is None:
            return
        await self.notifier(f"Promo draft (review before posting):\n\n{kit.render()}")


class Promoter:
    def __init__(self, notifier=None, link_base: str = "", enabled: bool = True):
        self.enabled = enabled
        self.link_base = link_base.rstrip("/")
        # Default: operator notification only. Append real publishers later.
        self.publishers: list[Publisher] = []
        if notifier is not None:
            self.publishers.append(OperatorNotifyPublisher(notifier))

    def _launch_link(self, result: LaunchResult) -> str:
        if result.pool_url:
            return result.pool_url
        if result.token_address and self.link_base:
            return f"{self.link_base}/{result.token_address}"
        return ""

    def build_kit(self, concept: Concept, result: LaunchResult) -> PromoKit:
        ticker = concept.paired_ticker
        one_liner = concept.thesis.split(".")[0].strip() or f"{concept.name} rides the {ticker} tape"
        disclaimer = f"Not affiliated with {ticker}. NFA."
        link = self._launch_link(result)
        # Compose a launch tweet, trimmed to a tweet-length budget, disclaimer kept.
        base = concept.launch_tweet or f"${concept.symbol} is live — riding the {ticker} tape."
        parts = [base]
        if result.token_address:
            parts.append(f"CA: {result.token_address}")
        if link:
            parts.append(link)
        if disclaimer not in base:
            parts.append(disclaimer)
        tweet = " ".join(parts)
        if len(tweet) > _TWEET_MAX:
            # Keep the disclaimer; trim the body.
            keep = f" CA: {result.token_address} {disclaimer}" if result.token_address else f" {disclaimer}"
            tweet = base[: _TWEET_MAX - len(keep) - 1].rstrip() + keep
        return PromoKit(
            symbol=concept.symbol,
            name=concept.name,
            one_liner=one_liner,
            tweet=tweet,
            launch_link=link,
            hashtags=[f"${concept.symbol}", f"${ticker}"],
        )

    # Backwards-compatible alias used elsewhere.
    def compose_announcement(self, concept: Concept, result: LaunchResult) -> str:
        return self.build_kit(concept, result).render()

    async def promote(self, concept: Concept, result: LaunchResult) -> PromoKit | None:
        if not self.enabled:
            return None
        kit = self.build_kit(concept, result)
        log.info("promo ready for $%s (link=%s)", concept.symbol, kit.launch_link or "none")
        for pub in self.publishers:
            try:
                await pub.publish(kit)
            except Exception as e:  # noqa: BLE001
                log.warning("publisher %s failed: %s", getattr(pub, "name", "?"), e)
        return kit
