"""Small thread-safe circuit breaker for repeatedly failing subsystems."""

from __future__ import annotations

import threading
import time
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpen(RuntimeError):
    pass


class CircuitBreaker:
    def __init__(self, failure_threshold=3, recovery_seconds=300, clock=None):
        self.failure_threshold = max(1, int(failure_threshold))
        self.recovery_seconds = max(0.0, float(recovery_seconds))
        self._clock = clock or time.monotonic
        self._failures = 0
        self._opened_at = None
        self._state = CircuitState.CLOSED
        self._lock = threading.Lock()

    @property
    def state(self):
        with self._lock:
            if (
                self._state == CircuitState.OPEN
                and self._opened_at is not None
                and self._clock() - self._opened_at >= self.recovery_seconds
            ):
                self._state = CircuitState.HALF_OPEN
            return self._state

    @property
    def failures(self):
        with self._lock:
            return self._failures

    def allow(self):
        state = self.state
        return state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def record_success(self):
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._state = CircuitState.CLOSED

    def record_failure(self):
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = self._clock()

    def trip(self):
        """Open immediately for a known permanent or expensive failure."""
        with self._lock:
            self._failures = max(self._failures, self.failure_threshold)
            self._state = CircuitState.OPEN
            self._opened_at = self._clock()

    def call(self, function, *args, **kwargs):
        if not self.allow():
            raise CircuitOpen("circuit is open; retry suppressed")
        try:
            result = function(*args, **kwargs)
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result

    def snapshot(self):
        return {
            "state": self.state.value,
            "failures": self.failures,
            "failure_threshold": self.failure_threshold,
            "recovery_seconds": self.recovery_seconds,
        }
