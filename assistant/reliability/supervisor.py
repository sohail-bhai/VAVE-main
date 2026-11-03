"""Background runtime supervisor with bounded failure retries."""

from __future__ import annotations

import logging
import threading

from assistant import config, events
from assistant.reliability import lifecycle
from assistant.reliability.circuit_breaker import CircuitBreaker
from assistant.reliability.diagnostics import RUNTIME_PROBES, _timed
from assistant.reliability.models import CheckStatus, HealthCheck, HealthReport

logger = logging.getLogger(__name__)

_thread = None
_stop = threading.Event()
_lock = threading.RLock()
_last_report = None
_last_level = None
_breakers = {}


def _breaker_for(name):
    with _lock:
        breaker = _breakers.get(name)
        if breaker is None:
            breaker = CircuitBreaker(
                failure_threshold=config.get_setting(
                    "reliability_failure_threshold", 3
                ),
                recovery_seconds=config.get_setting(
                    "reliability_recovery_seconds", 300
                ),
            )
            _breakers[name] = breaker
        return breaker


def run_once():
    """Run lightweight checks once and cache the report. Never raises."""
    global _last_report, _last_level
    checks = []
    for name, critical, probe in RUNTIME_PROBES:
        breaker = _breaker_for(name)
        if not breaker.allow():
            checks.append(HealthCheck(
                name=name,
                status=CheckStatus.WARNING,
                summary="repeated failing probe suppressed by circuit breaker",
                critical=critical,
                details={"circuit": breaker.snapshot()},
            ))
            continue
        check = _timed(name, critical, probe)
        checks.append(check)
        if check.status == CheckStatus.FAILED:
            breaker.record_failure()
        else:
            breaker.record_success()

    report = HealthReport(checks)
    with _lock:
        previous = _last_level
        _last_report = report
        _last_level = report.level

    if previous is not None and previous != report.level:
        try:
            bus = events.get_global_event_bus()
            if bus is not None:
                bus.emit(
                    events.EVENT_STATUS,
                    f"Runtime health changed: {report.level.value}",
                    scope="reliability",
                    level=report.level.value,
                )
        except Exception:
            logger.debug("Could not publish reliability event", exc_info=True)
    return report


def _loop(interval_seconds):
    while not _stop.wait(interval_seconds):
        try:
            run_once()
        except Exception:
            logger.debug("Reliability supervisor cycle failed", exc_info=True)


def start(interval_seconds=None):
    global _thread
    if interval_seconds is None:
        try:
            interval_seconds = float(
                config.get_setting("reliability_supervisor_interval_seconds", 60)
            )
        except (TypeError, ValueError):
            interval_seconds = 60.0
    if interval_seconds <= 0:
        return False
    with _lock:
        if _thread is not None and _thread.is_alive():
            return True
        _stop.clear()
        _thread = threading.Thread(
            target=_loop,
            args=(float(interval_seconds),),
            daemon=True,
            name="vave-reliability-supervisor",
        )
        _thread.start()
        lifecycle.register_thread("reliability_supervisor", _thread, stop)
        return True


def stop():
    global _thread
    _stop.set()
    with _lock:
        thread, _thread = _thread, None
    if (
        thread is not None
        and thread is not threading.current_thread()
        and thread.is_alive()
    ):
        thread.join(timeout=5)
    lifecycle.unregister("reliability_supervisor")


def running():
    with _lock:
        return _thread is not None and _thread.is_alive()


def latest_report():
    with _lock:
        return _last_report


def reset_for_tests():
    global _last_report, _last_level
    stop()
    with _lock:
        _last_report = None
        _last_level = None
        _breakers.clear()
