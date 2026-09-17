"""Regression tests for report.md / error_report.md — Architecture domain."""

import unittest
from unittest import mock


class _FakePlane:
    def __init__(self, granted=()):
        self.granted = set(granted)

    def has_capability(self, capability):
        return capability in self.granted


class Arch01GrantAwareGuardTests(unittest.TestCase):
    """ARCH-01: a live plane grant outranks static config tiers.

    Approving an action and denying it again at the tool call stranded
    approved steps. Hard-deny still wins; with no plane running, the tiers
    decide exactly as before.
    """

    def setUp(self):
        from assistant import call_context
        from assistant.control import service
        call_context.clear_taint()
        self.service = service
        self._real_plane = service._control_plane
        self.addCleanup(setattr, service, "_control_plane", self._real_plane)

    def test_live_grant_skips_confirm(self):
        from assistant import guard
        self.service._control_plane = _FakePlane({"google.gmail.send"})
        with mock.patch("assistant.confirm.ask") as mock_ask:
            allowed = guard._evaluate_policy(
                "send_email", {"to": "a@b.c"}, "telegram")
        self.assertTrue(allowed)
        mock_ask.assert_not_called()

    def test_no_plane_falls_back_to_tiers(self):
        from assistant import guard
        self.service._control_plane = None
        with mock.patch("assistant.confirm.ask",
                        return_value=False) as mock_ask:
            allowed = guard._evaluate_policy(
                "send_email", {"to": "a@b.c"}, "telegram")
        self.assertFalse(allowed)
        mock_ask.assert_called_once()

    def test_hard_deny_beats_live_grant(self):
        from assistant import guard
        self.service._control_plane = _FakePlane({"system.shell.run"})
        with mock.patch("assistant.confirm.ask") as mock_ask:
            allowed = guard._evaluate_policy(
                "run_terminal_command", {"command": "rm -rf /"}, "voice")
        self.assertFalse(allowed)
        mock_ask.assert_not_called()

    def test_tool_without_capability_uses_tiers(self):
        from assistant import guard
        self.service._control_plane = _FakePlane({"google.gmail.send"})
        allowed = guard._evaluate_policy("tell_time", {}, "voice")
        self.assertTrue(allowed)


class Arch02TaintIsolationTests(unittest.TestCase):
    """ARCH-02: taint must not leak across steps on reused pool workers."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from assistant.control.service import ControlPlane
        from assistant.control.store import ControlStore
        self.tempdir = tempfile.mkdtemp(prefix="vave-arch02-test-")
        self.store = ControlStore(Path(self.tempdir) / "control.db")
        self.plane = ControlPlane(store=self.store)

    def tearDown(self):
        self.store.close()
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_taint_does_not_leak_into_the_next_step(self):
        from assistant import call_context
        from assistant.control.executor import CancelToken, TaskExecutor
        task = self.plane.create_task("Two parts", steps=["First", "Second"])
        steps = self.plane.store.list_steps(task.id)
        seen = {}

        def runner(instruction, context):
            seen[instruction] = call_context.is_tainted()
            if instruction == "First":
                call_context.mark_tainted("web")
            return "done"

        ex = TaskExecutor(plane=self.plane, runner=runner, max_attempts=1)
        token = CancelToken()
        ex._run_step(task.id, steps[0], [], token)
        ex._run_step(task.id, steps[1], [], token)

        self.assertEqual({"First": False, "Second": False}, seen)
        self.assertFalse(call_context.is_tainted())

    def test_taint_still_guards_within_its_own_step(self):
        # Clearing happens between steps, never mid-step: taint set during a
        # step is visible for the rest of that step.
        from assistant import call_context
        from assistant.control.executor import CancelToken, TaskExecutor
        task = self.plane.create_task("One part", steps=["Only"])
        steps = self.plane.store.list_steps(task.id)
        seen = {}

        def runner(instruction, context):
            call_context.mark_tainted("web")
            seen[instruction] = call_context.is_tainted()
            return "done"

        ex = TaskExecutor(plane=self.plane, runner=runner, max_attempts=1)
        ex._run_step(task.id, steps[0], [], CancelToken())

        self.assertEqual({"Only": True}, seen)


class Arch03ConfigSafetyTests(unittest.TestCase):
    """ARCH-03: updates must not eat cached secrets or half-write the file."""

    def test_update_setting_leaves_the_cache_untouched(self):
        from assistant import config
        cache = {"user_name": "Old", "email_app_password": "hunter2"}
        with mock.patch.object(config, "load_config",
                               return_value=cache), \
             mock.patch.object(config, "save_config") as mock_save:
            config.update_setting("user_name", "New")
        self.assertEqual({"user_name": "Old",
                          "email_app_password": "hunter2"}, cache)
        saved = mock_save.call_args[0][0]
        self.assertEqual("New", saved["user_name"])
        self.assertNotIn("email_app_password", saved)

    def test_save_config_writes_complete_file(self):
        import json
        import tempfile
        from pathlib import Path
        from assistant import config
        with tempfile.TemporaryDirectory(prefix="vave-arch03-") as tmp:
            target = Path(tmp) / "config.json"
            with mock.patch.object(config, "CONFIG_FILE", target):
                config.save_config({"a": 1})
            self.assertEqual({"a": 1},
                             json.loads(target.read_text(encoding="utf-8")))
            self.assertEqual([], list(Path(tmp).glob(".config.*.tmp")))

    def test_save_config_cleans_up_after_a_failed_replace(self):
        import tempfile
        from pathlib import Path
        from assistant import config
        with tempfile.TemporaryDirectory(prefix="vave-arch03-") as tmp:
            target = Path(tmp) / "config.json"
            with mock.patch.object(config, "CONFIG_FILE", target), \
                 mock.patch("os.replace",
                            side_effect=OSError("disk vanished")):
                with self.assertRaises(OSError):
                    config.save_config({"a": 1})
            self.assertFalse(target.exists())
            self.assertEqual([], list(Path(tmp).glob(".config.*.tmp")))


if __name__ == "__main__":
    unittest.main()
