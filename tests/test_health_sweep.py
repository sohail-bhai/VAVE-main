"""Unit tests for the background health sweep thread."""

import time
import unittest
from unittest import mock

from assistant import health_sweep


class HealthSweepTests(unittest.TestCase):
    def setUp(self):
        health_sweep.stop()

    def tearDown(self):
        health_sweep.stop()

    def test_zero_interval_disables(self):
        self.assertFalse(health_sweep.start(0))
        self.assertFalse(health_sweep.running())

    def test_start_is_idempotent(self):
        self.assertTrue(health_sweep.start(60))
        first = health_sweep._thread
        self.assertTrue(health_sweep.start(60))
        self.assertIs(first, health_sweep._thread)
        self.assertTrue(health_sweep.running())

    def test_sweep_calls_plane_sweep(self):
        plane = mock.Mock()
        with mock.patch("assistant.control.service._control_plane", plane):
            self.assertTrue(health_sweep.start(0.05))
            deadline = time.time() + 5.0
            while time.time() < deadline and plane.sweep.call_count == 0:
                time.sleep(0.05)
        self.assertGreaterEqual(plane.sweep.call_count, 1)

    def test_plane_exceptions_are_swallowed(self):
        plane = mock.Mock()
        plane.sweep.side_effect = RuntimeError("store exploded")
        with mock.patch("assistant.control.service._control_plane", plane):
            self.assertTrue(health_sweep.start(0.05))
            time.sleep(0.3)
        # No exception escaped the thread; it kept running until stopped.
        self.assertTrue(health_sweep.running())

    def test_missing_plane_is_skipped_quietly(self):
        with mock.patch("assistant.control.service._control_plane", None):
            self.assertTrue(health_sweep.start(0.05))
            time.sleep(0.2)
        self.assertTrue(health_sweep.running())


if __name__ == "__main__":
    unittest.main()
