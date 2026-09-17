"""Unit tests for Phase 1 Command Intelligence: personal voice shortcuts."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from assistant.commands import execute_command, handle_shortcut_command, apply_shortcut
from assistant.api.app import create_app
from assistant.api.auth import ApiSecurity
from assistant.control.service import ControlPlane
from assistant.control.store import ControlStore


class ShortcutStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="vave-shortcut-test-")
        self.store = ControlStore(Path(self.tempdir) / "control.db")

    def tearDown(self):
        self.store.close()
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_shortcut_crud_roundtrip(self):
        self.assertTrue(self.store.save_command_shortcut("Coffee Mode", "open youtube"))
        saved = self.store.get_command_shortcut("coffee mode")
        self.assertIsNotNone(saved)
        self.assertEqual("open youtube", saved["expansion"])
        self.assertEqual(0, saved["use_count"])

        self.store.record_command_shortcut_use("coffee mode")
        self.assertEqual(1, self.store.get_command_shortcut("coffee mode")["use_count"])

        self.assertEqual(1, len(self.store.list_command_shortcuts()))

        # Replacing keeps the trigger but swaps the expansion.
        self.store.save_command_shortcut("coffee mode", "open spotify")
        self.assertEqual("open spotify", self.store.get_command_shortcut("coffee mode")["expansion"])
        self.assertEqual(1, len(self.store.list_command_shortcuts()))

        self.assertTrue(self.store.delete_command_shortcut("coffee mode"))
        self.assertIsNone(self.store.get_command_shortcut("coffee mode"))
        self.assertFalse(self.store.delete_command_shortcut("coffee mode"))

    def test_empty_trigger_or_expansion_refused(self):
        self.assertFalse(self.store.save_command_shortcut("", "open youtube"))
        self.assertFalse(self.store.save_command_shortcut("coffee", "   "))


class ShortcutCommandTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="vave-shortcut-cmd-test-")
        self.store = ControlStore(Path(self.tempdir) / "control.db")
        self.plane = ControlPlane(store=self.store)

    def tearDown(self):
        self.store.close()
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def _run(self, command, **extra_patches):
        with mock.patch("assistant.control.service.get_control_plane", return_value=self.plane):
            with mock.patch("assistant.commands.speak") as speak:
                with mock.patch("assistant.commands.webbrowser.open") as open_web:
                    result = execute_command(command)
                    return result, speak, open_web

    def test_create_and_use_shortcut(self):
        result, speak, _ = self._run("when i say coffee mode do open youtube")
        self.assertTrue(result)
        self.assertEqual("open youtube", self.store.get_command_shortcut("coffee mode")["expansion"])

        result, speak, open_web = self._run("coffee mode")
        self.assertTrue(result)
        open_web.assert_called_once()
        self.assertIn("youtube", open_web.call_args[0][0])
        self.assertEqual(1, self.store.get_command_shortcut("coffee mode")["use_count"])

    def test_reserved_trigger_refused(self):
        result, speak, _ = self._run("when i say what time is it do open youtube")
        self.assertTrue(result)
        self.assertIsNone(self.store.get_command_shortcut("what time is it"))
        self.assertIn("already", speak.call_args[0][0])

    def test_shortcut_may_not_point_at_shortcut(self):
        self.store.save_command_shortcut("focus", "open youtube")
        result, speak, _ = self._run("when i say work mode do focus")
        self.assertTrue(result)
        self.assertIsNone(self.store.get_command_shortcut("work mode"))

    def test_delete_and_list_shortcuts(self):
        self.store.save_command_shortcut("coffee mode", "open youtube")
        result, speak, _ = self._run("delete shortcut coffee mode")
        self.assertTrue(result)
        self.assertIsNone(self.store.get_command_shortcut("coffee mode"))

        self.store.save_command_shortcut("focus", "open youtube")
        result, speak, _ = self._run("list my shortcuts")
        self.assertTrue(result)
        self.assertIn("focus", speak.call_args[0][0])

    def test_non_shortcut_command_untouched(self):
        with mock.patch("assistant.control.service.get_control_plane", return_value=self.plane):
            with mock.patch("assistant.commands.speak"):
                with mock.patch("assistant.commands.tell_time") as tell_time:
                    self.assertTrue(execute_command("what time is it"))
        tell_time.assert_called_once()
        self.assertEqual(0, len(self.store.list_command_shortcuts()))


class ShortcutApiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="vave-shortcut-api-test-")
        self.store = ControlStore(Path(self.tempdir) / "control.db")
        self.plane = ControlPlane(store=self.store)
        self.guard = ApiSecurity(self.store, require_auth=True, trust_local=False)
        self.client = TestClient(create_app(control=self.plane, security=self.guard))

        code = self.guard.issue_pairing_code()["code"]
        claim = self.client.post("/api/pair", json={"code": code, "name": "ShortcutPhone"})
        self.headers = {"Authorization": f"Bearer {claim.json()['token']}"}

    def tearDown(self):
        self.store.close()
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_shortcut_endpoints_roundtrip(self):
        res = self.client.post("/api/commands/shortcuts",
                               json={"trigger": "Coffee Mode", "expansion": "open youtube"},
                               headers=self.headers)
        self.assertEqual(200, res.status_code)
        self.assertEqual("open youtube", res.json()["expansion"])

        listing = self.client.get("/api/commands/shortcuts", headers=self.headers)
        self.assertEqual(200, listing.status_code)
        self.assertEqual(1, len(listing.json()["shortcuts"]))

        gone = self.client.delete("/api/commands/shortcuts/Coffee Mode", headers=self.headers)
        self.assertEqual(200, gone.status_code)
        self.assertEqual(0, len(self.client.get("/api/commands/shortcuts", headers=self.headers).json()["shortcuts"]))

        missing = self.client.delete("/api/commands/shortcuts/nope", headers=self.headers)
        self.assertEqual(404, missing.status_code)

    def test_chained_shortcut_refused(self):
        self.client.post("/api/commands/shortcuts",
                         json={"trigger": "focus", "expansion": "open youtube"},
                         headers=self.headers)
        res = self.client.post("/api/commands/shortcuts",
                               json={"trigger": "work", "expansion": "focus"},
                               headers=self.headers)
        self.assertEqual(400, res.status_code)

    def test_frequent_commands_endpoint(self):
        self.store.record_command_usage("time", category="system")
        self.store.record_command_usage("time", category="system")
        res = self.client.get("/api/commands/frequent", headers=self.headers)
        self.assertEqual(200, res.status_code)
        self.assertEqual("time", res.json()["commands"][0]["command_pattern"])
        self.assertEqual(2, res.json()["commands"][0]["call_count"])


if __name__ == "__main__":
    unittest.main()
