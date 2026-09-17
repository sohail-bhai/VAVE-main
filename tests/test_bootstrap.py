"r""Tests for centralized safety bootstrap."""

import os
from pathlib import Path
from unittest import mock

from tests.support import VaveTestCase
from assistant import events
from assistant import audit
from assistant import guard
from assistant import confirm
from assistant import overwatch
from assistant.bootstrap import (bootstrap_safety, is_bootstrapped,
                                 reset_bootstrap, resume_interrupted_tasks)


class TestBootstrap(VaveTestCase):

    def setUp(self):
        super().setUp()
        reset_bootstrap()

    def tearDown(self):
        reset_bootstrap()
        super().tearDown()

    def test_bootstrap_safety_initializes_all_subsystems(self):
        self.assertFalse(is_bootstrapped())

        bus = bootstrap_safety()

        self.assertTrue(is_bootstrapped())
        self.assertIsNotNone(bus)
        self.assertIs(events.get_global_event_bus(), bus)
        self.assertIs(guard._bus, bus)
        self.assertIs(confirm._broker._bus, bus)

        # Audit path should be isolated to temp_dir (from VaveTestCase VAVE_DATA_DIR)
        self.assertIsNotNone(audit._audit_path)
        self.assertTrue(str(audit._audit_path).startswith(self.temp_dir))

    def test_bootstrap_safety_custom_bus_and_path(self):
        custom_bus = events.EventBus(maxsize=500)
        custom_path = Path(self.temp_dir) / "custom_audit.jsonl"

        bus = bootstrap_safety(event_bus=custom_bus, audit_path=custom_path)

        self.assertIs(bus, custom_bus)
        self.assertEqual(audit._audit_path, custom_path)
        self.assertIs(guard._bus, custom_bus)
        self.assertTrue(is_bootstrapped())

    def test_resume_interrupted_tasks_counts_and_never_raises(self):
        fake_executor = mock.MagicMock()
        fake_executor.resume_interrupted.return_value = [object(), object()]
        with mock.patch("assistant.control.executor.get_executor",
                        return_value=fake_executor):
            self.assertEqual(2, resume_interrupted_tasks())

    def test_resume_interrupted_tasks_empty_when_nothing_pending(self):
        fake_executor = mock.MagicMock()
        fake_executor.resume_interrupted.return_value = []
        with mock.patch("assistant.control.executor.get_executor",
                        return_value=fake_executor):
            self.assertEqual(0, resume_interrupted_tasks())

    def test_resume_interrupted_tasks_survives_executor_failure(self):
        with mock.patch("assistant.control.executor.get_executor",
                        side_effect=RuntimeError("store is gone")):
            self.assertEqual(0, resume_interrupted_tasks())
