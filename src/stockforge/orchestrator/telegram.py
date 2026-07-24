"""Telegram control plane (raw Bot API over httpx — no heavy SDK).

Provides:
  * send(text) / send_with_buttons(text, buttons)
  * long-poll getUpdates loop dispatching /commands and approval callbacks
  * an async approval primitive: request_approval() blocks until a human taps
    Approve/Reject (or times out).

Security: only messages/callbacks from TELEGRAM_CHAT_ID are honored. Everything
else is ignored. If Telegram isn't configured, this degrades to a no-op that
auto-denies approvals (fail-closed) so nothing money-moving slips through.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import httpx

from ..logging import get_logger

log = get_logger("telegram")

CommandHandler = Callable[[str], Awaitable[str]]


class TelegramControl:
    def __init__(self, bot_token: str, chat_id: str, approval_timeout: float = 900.0):
        self.bot_token = bot_token
        self.chat_id = str(chat_id)
        self.enabled = bool(bot_token and chat_id)
        self.approval_timeout = approval_timeout
        self._base = f"https://api.telegram.org/bot{bot_token}"
        self._client: httpx.AsyncClient | None = None
        self._offset = 0
        self._commands: dict[str, CommandHandler] = {}
        # pending approvals: approval_id -> Future[bool]
        self._pending: dict[str, asyncio.Future] = {}
        self._stop = asyncio.Event()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=40.0)
        return self._client

    def command(self, name: str) -> Callable[[CommandHandler], CommandHandler]:
        def deco(fn: CommandHandler) -> CommandHandler:
            self._commands[name] = fn
            return fn

        return deco

    def register(self, name: str, handler: CommandHandler) -> None:
        self._commands[name] = handler

    # ---- outbound ------------------------------------------------------------
    async def send(self, text: str) -> None:
        if not self.enabled:
            log.info("[telegram-disabled] %s", text.replace("\n", " | ")[:300])
            return
        try:
            await self.client.post(
                f"{self._base}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True},
            )
        except Exception as e:  # noqa: BLE001
            log.warning("telegram send failed: %s", e)

    async def _send_buttons(self, text: str, approval_id: str) -> None:
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve", "callback_data": f"approve:{approval_id}"},
                    {"text": "🛑 Reject", "callback_data": f"reject:{approval_id}"},
                ]
            ]
        }
        await self.client.post(
            f"{self._base}/sendMessage",
            json={"chat_id": self.chat_id, "text": text, "reply_markup": keyboard},
        )

    # ---- approvals -----------------------------------------------------------
    async def request_approval(self, approval_id: str, summary: str) -> bool:
        """Block until a human approves/rejects, or time out (fail-closed)."""
        if not self.enabled:
            log.warning("approval '%s' auto-DENIED (telegram disabled, fail-closed)", approval_id)
            return False
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[approval_id] = fut
        await self._send_buttons(f"🔐 Approval required\n\n{summary}", approval_id)
        try:
            return await asyncio.wait_for(fut, timeout=self.approval_timeout)
        except TimeoutError:
            log.warning("approval '%s' timed out -> DENIED", approval_id)
            await self.send(f"⌛ Approval for {approval_id} timed out — not executed.")
            return False
        finally:
            self._pending.pop(approval_id, None)

    def _resolve(self, approval_id: str, decision: bool) -> bool:
        fut = self._pending.get(approval_id)
        if fut and not fut.done():
            fut.set_result(decision)
            return True
        return False

    # ---- inbound loop --------------------------------------------------------
    async def run(self) -> None:
        if not self.enabled:
            log.info("telegram control plane disabled (no token/chat_id)")
            return
        log.info("telegram control plane online")
        await self.send("🟢 StockForge online. /status /launch /claim /pause /resume /help")
        while not self._stop.is_set():
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                log.warning("telegram poll error: %s", e)
                await asyncio.sleep(3)

    async def _poll_once(self) -> None:
        r = await self.client.get(
            f"{self._base}/getUpdates",
            params={"offset": self._offset, "timeout": 30},
        )
        data = r.json()
        for update in data.get("result", []):
            self._offset = update["update_id"] + 1
            await self._dispatch(update)

    async def _dispatch(self, update: dict) -> None:
        if "callback_query" in update:
            await self._on_callback(update["callback_query"])
            return
        msg = update.get("message") or update.get("channel_post")
        if not msg:
            return
        if str(msg.get("chat", {}).get("id")) != self.chat_id:
            log.warning("ignoring message from unauthorized chat %s", msg.get("chat", {}).get("id"))
            return
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            return
        cmd, _, arg = text.partition(" ")
        cmd = cmd.lstrip("/").split("@")[0].lower()
        handler = self._commands.get(cmd)
        if handler is None:
            await self.send(f"Unknown command: /{cmd}. Try /help")
            return
        try:
            reply = await handler(arg.strip())
            if reply:
                await self.send(reply)
        except Exception as e:  # noqa: BLE001
            log.exception("command /%s failed", cmd)
            await self.send(f"⚠️ /{cmd} failed: {e}")

    async def _on_callback(self, cq: dict) -> None:
        if str(cq.get("message", {}).get("chat", {}).get("id")) != self.chat_id:
            return
        data = cq.get("data", "")
        action, _, approval_id = data.partition(":")
        decision = action == "approve"
        resolved = self._resolve(approval_id, decision)
        # Acknowledge the tap so the button spinner clears.
        try:
            await self.client.post(
                f"{self._base}/answerCallbackQuery",
                json={
                    "callback_query_id": cq["id"],
                    "text": ("Approved ✅" if decision else "Rejected 🛑")
                    + ("" if resolved else " (already resolved/expired)"),
                },
            )
        except Exception:  # noqa: BLE001
            pass

    async def stop(self) -> None:
        self._stop.set()
        if self._client is not None:
            await self._client.aclose()
            self._client = None
