"""Unified launcher facade.

Responsibilities:
  * Pick the backend (cli | rest) from config.
  * Enforce the dry-run master switch (NOTHING broadcasts when dry_run=True).
  * Wrap the real call in the circuit breaker.

Approval + rate limiting are enforced one level up (orchestrator), so this class
stays a clean "given a request, produce a result" unit that's easy to test.
"""

from __future__ import annotations

from ..circuit import CircuitBreaker
from ..config import Settings
from ..logging import get_logger
from ..models import LaunchRequest, LaunchResult, LaunchStatus, PairStatus
from .bankr_cli import BankrCliBackend
from .bankr_rest import BankrRestBackend
from .base import build_cli_args, build_launch_prompt
from .pairing import CLI_SUPPORTS_STOCK_PAIR, classify_pairing, find_quote_labels

log = get_logger("launcher")

_PAIR_EMOJI = {
    PairStatus.ACCEPTED: "🎯",
    PairStatus.DEGRADED: "↘️",
    PairStatus.REJECTED: "❌",
    PairStatus.REQUESTED: "❓",
    PairStatus.NOT_REQUESTED: "·",
}


class BankrLauncher:
    def __init__(self, settings: Settings, breaker: CircuitBreaker | None = None):
        self.settings = settings
        self.breaker = breaker or CircuitBreaker("bankr", failure_threshold=3, reset_timeout=300)

    def _backend(self):
        if self.settings.bankr_backend == "cli":
            return BankrCliBackend(
                binary=self.settings.bankr_cli_bin,
                simulate=self.settings.dry_run,
            )
        return BankrRestBackend(
            base_url=self.settings.bankr_api_base,
            api_key=self.settings.bankr_api_key,
        )

    def preview(self, req: LaunchRequest) -> dict:
        """Human-readable preview of exactly what will be sent — for approvals."""
        pair_note = ""
        if req.pair_with:
            if self.settings.bankr_backend == "cli" and not CLI_SUPPORTS_STOCK_PAIR:
                pair_note = "CLI has no pairing flag → will use standard pool (use rest backend)"
            elif req.chain != "robinhood":
                pair_note = "stock-pairing only applies on robinhood chain"
            else:
                pair_note = "expressed via NL prompt; UNVERIFIED — outcome classified after launch"
        return {
            "backend": self.settings.bankr_backend,
            "dry_run": self.settings.dry_run,
            "chain": req.chain,
            "name": req.name,
            "symbol": req.symbol,
            "pair_with": req.pair_with or "(default WETH)",
            "pair_note": pair_note or "n/a",
            "prompt": build_launch_prompt(req),
            "cli_args": build_cli_args(req, simulate=self.settings.dry_run),
        }

    def _warn_cli_pairing(self, req: LaunchRequest) -> None:
        """The CLI has no verified pairing flag; warn when we can't express intent."""
        if req.pair_with and self.settings.bankr_backend == "cli" and not CLI_SUPPORTS_STOCK_PAIR:
            log.warning(
                "stock-pair '%s' requested but the bankr CLI has no pairing flag — this "
                "launch will use a standard pool. Use BANKR_BACKEND=rest to express pairing.",
                req.pair_with.upper(),
            )

    def _finalize_pairing(self, req: LaunchRequest, result: LaunchResult) -> LaunchResult:
        """Attach the best-effort stock-pairing verdict + clear logging."""
        result.pair_requested = req.pair_with
        result.quote_labels = find_quote_labels(result.raw)
        result.pair_status = classify_pairing(
            req.pair_with, result.status, result.token_address, result.quote_labels
        )
        if req.pair_with:
            log.info(
                "%s stock-pair %s -> %s (labels=%s, status=%s)",
                _PAIR_EMOJI.get(result.pair_status, "?"),
                req.pair_with.upper(),
                result.pair_status.value,
                result.quote_labels or "none",
                result.status.value,
            )
        return result

    async def launch(self, req: LaunchRequest) -> LaunchResult:
        self._warn_cli_pairing(req)

        # Dry-run master switch: for REST we cannot guarantee simulation, so we
        # refuse to broadcast and return a SIMULATED result instead.
        if self.settings.dry_run and self.settings.bankr_backend == "rest":
            log.warning("[DRY-RUN] REST launch suppressed for %s (%s)", req.symbol, req.name)
            result = LaunchResult(
                request_id=req.id,
                status=LaunchStatus.SIMULATED,
                raw={"preview": self.preview(req)},
            )
            return self._finalize_pairing(req, result)

        self.breaker.raise_if_open()
        backend = self._backend()
        try:
            if isinstance(backend, BankrRestBackend):
                async with backend:
                    result = await backend.launch(req)
            else:
                result = await backend.launch(req)
        except Exception as e:  # noqa: BLE001
            self.breaker.record_failure()
            log.exception("launch backend raised")
            result = LaunchResult(request_id=req.id, status=LaunchStatus.FAILED, error=str(e))
            return self._finalize_pairing(req, result)

        if result.status in (LaunchStatus.FAILED, LaunchStatus.REJECTED):
            self.breaker.record_failure()
        else:
            self.breaker.record_success()
        return self._finalize_pairing(req, result)
