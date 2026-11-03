"""Reliability models, circuit breaker, lifecycle, and supervisor tests."""

import json
import threading
import time
import unittest
from io import StringIO
from unittest import mock

from assistant.reliability import lifecycle, supervisor
from assistant.reliability.circuit_breaker import (
    CircuitBreaker,
    CircuitOpen,
    CircuitState,
)
from assistant.reliability.diagnostics import doctor_cli
from assistant.reliability.models import (
    CheckStatus,
    HealthCheck,
    HealthLevel,
    HealthReport,
)
from assistant.reliability.instance import InstanceLock


class HealthModelTests(unittest.TestCase):
    def test_critical_failure_sets_failed_level(self):
        report = HealthReport([
            HealthCheck("optional", CheckStatus.WARNING, "slow"),
            HealthCheck("core", CheckStatus.FAILED, "down", critical=True),
        ])
        self.assertEqual(HealthLevel.FAILED, report.level)
        self.assertEqual(1, report.exit_code)
        self.assertEqual("failed", report.to_dict()["level"])

    def test_optional_warning_is_degraded_not_failed(self):
        report = HealthReport([
            HealthCheck("optional", CheckStatus.WARNING, "not configured")
        ])
        self.assertEqual(HealthLevel.DEGRADED, report.level)
        self.assertEqual(0, report.exit_code)


class CircuitBreakerTests(unittest.TestCase):
    def test_opens_and_half_opens_after_cooldown(self):
        now = [100.0]
        breaker = CircuitBreaker(
            failure_threshold=2,
            recovery_seconds=10,
            clock=lambda: now[0],
        )
        breaker.record_failure()
        self.assertEqual(CircuitState.CLOSED, breaker.state)
        breaker.record_failure()
        self.assertEqual(CircuitState.OPEN, breaker.state)
        with self.assertRaises(CircuitOpen):
            breaker.call(lambda: None)
        now[0] += 11
        self.assertEqual(CircuitState.HALF_OPEN, breaker.state)
        breaker.call(lambda: "ok")
        self.assertEqual(CircuitState.CLOSED, breaker.state)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        lifecycle.clear_for_tests()

    def tearDown(self):
        lifecycle.shutdown_all(join_timeout=1)
        lifecycle.clear_for_tests()

    def test_shutdown_stops_and_joins_registered_thread(self):
        stop_event = threading.Event()
        thread = threading.Thread(
            target=lambda: stop_event.wait(5),
            daemon=True,
            name="managed-test-thread",
        )
        thread.start()
        lifecycle.register_thread("test", thread, stop_event.set)
        remaining = lifecycle.shutdown_all(join_timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual([], remaining)


class InstanceLockTests(unittest.TestCase):
    def test_duplicate_role_is_refused_until_owner_releases(self):
        import tempfile

        with tempfile.TemporaryDirectory(prefix="vave-instance-") as root:
            first = InstanceLock("gui", root)
            second = InstanceLock("gui", root)
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            self.assertEqual(first.owner_pid, second.owner_pid)
            first.release()
            self.assertTrue(second.acquire())
            second.release()

    def test_stale_lock_is_reclaimed(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory(prefix="vave-instance-") as root:
            path = Path(root) / ".vave-server.pid"
            path.write_text('{"pid": 99999999}', encoding="utf-8")
            lock = InstanceLock("server", root)
            with mock.patch(
                "assistant.reliability.instance._pid_alive",
                return_value=False,
            ):
                self.assertTrue(lock.acquire())
            lock.release()


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        supervisor.reset_for_tests()
        lifecycle.clear_for_tests()

    def tearDown(self):
        supervisor.reset_for_tests()
        lifecycle.clear_for_tests()

    def test_repeated_failure_opens_probe_circuit(self):
        calls = []

        def failed_probe():
            calls.append(1)
            return HealthCheck("broken", CheckStatus.FAILED, "down", critical=True)

        with mock.patch.object(
            supervisor,
            "RUNTIME_PROBES",
            (("broken", True, failed_probe),),
        ), mock.patch.object(
            supervisor.config,
            "get_setting",
            side_effect=lambda key, default=None: 2
            if key == "reliability_failure_threshold"
            else 300,
        ):
            supervisor.run_once()
            supervisor.run_once()
            report = supervisor.run_once()
        self.assertEqual(2, len(calls))
        self.assertIn("suppressed", report.checks[0].summary)

    def test_start_is_idempotent_and_registered(self):
        self.assertTrue(supervisor.start(60))
        first = supervisor._thread
        self.assertTrue(supervisor.start(60))
        self.assertIs(first, supervisor._thread)
        names = {item["name"] for item in lifecycle.snapshot()}
        self.assertIn("reliability_supervisor", names)


class SubsystemCircuitTests(unittest.TestCase):
    def test_invalid_google_grant_is_not_retried_until_reconnect(self):
        from assistant.workspace import auth

        auth._refresh_breaker = None
        auth._refresh_permanent_failure = False
        creds = mock.Mock()
        creds.refresh.side_effect = RuntimeError("invalid_grant: revoked")
        self.assertIsNone(auth._try_refresh(creds, object()))
        self.assertIsNone(auth._try_refresh(creds, object()))
        self.assertEqual(1, creds.refresh.call_count)
        auth._reset_refresh_circuit()

    def test_wakeword_microphone_failure_opens_circuit_and_stops_cleanly(self):
        from assistant import wakeword

        wakeword.stop_wakeword_engine()
        audio = mock.Mock()
        audio.open.side_effect = OSError("no microphone")

        def setting(key, default=None):
            values = {
                "wakeword_threshold": 0.6,
                "reliability_failure_threshold": 1,
                "reliability_recovery_seconds": 300,
            }
            return values.get(key, default)

        with mock.patch.object(
            wakeword,
            "_load_wakeword_model",
            return_value=mock.Mock(),
        ), mock.patch.object(
            wakeword.pyaudio,
            "PyAudio",
            return_value=audio,
        ), mock.patch(
            "assistant.config.get_setting",
            side_effect=setting,
        ):
            wakeword.start_wakeword_engine(lambda: None)
            deadline = time.time() + 2
            while audio.open.call_count == 0 and time.time() < deadline:
                time.sleep(0.01)
            wakeword.stop_wakeword_engine()

        self.assertEqual(1, audio.open.call_count)
        audio.terminate.assert_called_once()
        self.assertIsNone(wakeword._wakeword_thread)

    def test_speech_worker_registers_and_stops_cleanly(self):
        from assistant import speech

        speech.stop_speech_thread()
        speech.start_speech_thread()
        self.assertTrue(speech._speech_thread.is_alive())
        names = {item["name"] for item in lifecycle.snapshot()}
        self.assertIn("speech_output", names)
        speech.stop_speech_thread()
        self.assertIsNone(speech._speech_thread)
        names = {item["name"] for item in lifecycle.snapshot()}
        self.assertNotIn("speech_output", names)


class DoctorOutputTests(unittest.TestCase):
    def test_json_output_is_machine_readable(self):
        report = HealthReport([
            HealthCheck("config", CheckStatus.OK, "valid", critical=True)
        ])
        stream = StringIO()
        with mock.patch(
            "assistant.reliability.diagnostics.run_doctor",
            return_value=report,
        ):
            code = doctor_cli(json_output=True, stream=stream)
        self.assertEqual(0, code)
        self.assertEqual("healthy", json.loads(stream.getvalue())["level"])


if __name__ == "__main__":
    unittest.main()
