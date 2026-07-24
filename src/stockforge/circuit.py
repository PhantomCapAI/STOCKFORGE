"""Circuit breaker. Trips after N consecutive failures and stays open for a cooldown.

Used to wrap Bankr calls so a run of failures (API down, key revoked, insufficient
funds) halts launches instead of burning the daily budget on doomed attempts.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from .logging import get_logger

log = get_logger("circuit")


class State(str, Enum):
    CLOSED = "closed"  # healthy
    OPEN = "open"  # tripped, rejecting
    HALF_OPEN = "half_open"  # probing recovery


class CircuitOpenError(RuntimeError):
    pass


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 3
    reset_timeout: float = 300.0  # seconds open before probing
    _state: State = State.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0
    _consecutive: int = field(default=0, repr=False)

    @property
    def state(self) -> State:
        if self._state is State.OPEN and (time.time() - self._opened_at) >= self.reset_timeout:
            self._state = State.HALF_OPEN
            log.warning("circuit '%s' -> HALF_OPEN (probing)", self.name)
        return self._state

    def allow(self) -> bool:
        return self.state is not State.OPEN

    def raise_if_open(self) -> None:
        if not self.allow():
            retry = self.reset_timeout - (time.time() - self._opened_at)
            raise CircuitOpenError(f"circuit '{self.name}' open; retry in {retry:.0f}s")

    def record_success(self) -> None:
        if self._state in (State.HALF_OPEN, State.OPEN):
            log.info("circuit '%s' -> CLOSED (recovered)", self.name)
        self._state = State.CLOSED
        self._consecutive = 0
        self._failures = 0

    def record_failure(self) -> None:
        self._failures += 1
        self._consecutive += 1
        if self._consecutive >= self.failure_threshold:
            if self._state is not State.OPEN:
                log.error(
                    "circuit '%s' -> OPEN after %d consecutive failures",
                    self.name,
                    self._consecutive,
                )
            self._state = State.OPEN
            self._opened_at = time.time()

    def snapshot(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "consecutive_failures": self._consecutive,
            "total_failures": self._failures,
        }
