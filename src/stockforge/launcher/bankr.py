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
from ..models import LaunchRequest, LaunchResult, LaunchStatus
from .bankr_cli import BankrCliBackend
from .bankr_rest import BankrRestBackend
from .base import build_cli_args, build_launch_prompt

log = get_logger("launcher")


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
        return {
            "backend": self.settings.bankr_backend,
            "dry_run": self.settings.dry_run,
            "chain": req.chain,
            "name": req.name,
            "symbol": req.symbol,
            "pair_with": req.pair_with or "(default WETH)",
            "prompt": build_launch_prompt(req),
            "cli_args": build_cli_args(req, simulate=self.settings.dry_run),
        }

    async def launch(self, req: LaunchRequest) -> LaunchResult:
        # Dry-run master switch: for REST we cannot guarantee simulation, so we
        # refuse to broadcast and return a SIMULATED result instead.
        if self.settings.dry_run and self.settings.bankr_backend == "rest":
            log.warning("[DRY-RUN] REST launch suppressed for %s (%s)", req.symbol, req.name)
            return LaunchResult(
                request_id=req.id,
                status=LaunchStatus.SIMULATED,
                raw={"preview": self.preview(req)},
            )

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
            return LaunchResult(request_id=req.id, status=LaunchStatus.FAILED, error=str(e))

        if result.status in (LaunchStatus.FAILED, LaunchStatus.REJECTED):
            self.breaker.record_failure()
        else:
            self.breaker.record_success()
        return result
