"r""Tests for centralized safety bootstrap."""

import os
from pathlib import Path

from tests.support import VaveTestCase
from assistant import events
from assistant import audit
from assistant import guard
from assistant import confirm
from assistant import overwatch
from assistant.bootstrap import bootstrap_safety, is_bootstrapped, reset_bootstrap


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
