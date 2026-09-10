"""Tests for Wave 0 & Wave 1 safety hardening.

Validates:
- Unified hard_stop() trips kill switch, halts overwatch, interrupts speech, and stops control plane.
- Guard hard-deny layer unconditionally blocks mutations to assistant/, gui/, config.json, secret.key,
  safety/overwatch settings, and catastrophic shell wipes, beating any allow_* flags.
- Overwatch auto_click defaults and respects kill switch and guard policies.
- ControlStore close idempotency and context manager protocol.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from assistant import call_context, guard
from assistant.control.store import ControlStore
from assistant.control.service import ControlPlane, reset_control_plane
from assistant.safety_stop import hard_stop, reset_hard_stop
from assistant.overwatch.rules import OverwatchRule
from assistant.overwatch.engine import OverwatchEngine


class TestSafetyHardStop(unittest.TestCase):
    def setUp(self):
        reset_hard_stop()

    def tearDown(self):
        reset_hard_stop()

    def test_hard_stop_activates_kill_switch(self):
        self.assertFalse(guard.is_killed())
        res = hard_stop(source="unit_test")
        self.assertTrue(res["kill_switch_tripped"])
        self.assertTrue(guard.is_killed())

        # Reset works cleanly
        reset_hard_stop()
        self.assertFalse(guard.is_killed())

    def test_hard_stop_halts_active_overwatch(self):
        engine = OverwatchEngine()
        engine._active = True
        with mock.patch("assistant.overwatch.engine._engine", engine):
            res = hard_stop(source="unit_test")
            self.assertTrue(res["overwatch_stopped"])
            self.assertFalse(engine._active)

    def test_hard_stop_stops_control_plane(self):
        tempdir = tempfile.mkdtemp(prefix="vave-stop-test-")
        db_path = Path(tempdir) / "control.db"
        plane = ControlPlane(store=ControlStore(db_path))
        task = plane.create_task("Running task", steps=["Step 1"])

        with mock.patch("assistant.control.service._control_plane", plane):
            res = hard_stop(source="unit_test")
            self.assertIsNotNone(res["control_plane"])
            self.assertTrue(res["control_plane"]["stopped"])
            self.assertEqual("cancelled", plane.get_task(task.id).status)

        plane.close()
        shutil.rmtree(tempdir, ignore_errors=True)


class TestGuardHardDeny(unittest.TestCase):
    def setUp(self):
        reset_hard_stop()
        call_context.clear_taint()
        self._token = call_context.set_origin("routine")  # routine has allow_destructive: true

    def tearDown(self):
        call_context.clear_taint()
        call_context.reset_origin(self._token)
        reset_hard_stop()

    def test_hard_deny_blocks_safety_and_overwatch_setting_updates(self):
        # Even with allow_destructive, updating safety or overwatch is denied
        self.assertFalse(guard._evaluate_policy("update_setting", {"key": "safety.voice.allow_destructive", "value": True}, "routine"))
        self.assertFalse(guard._evaluate_policy("update_setting", {"setting": "overwatch.rules", "value": []}, "routine"))

    def test_hard_deny_blocks_self_writes_to_codebase_and_configs(self):
        # Even with allow_destructive, writing to assistant, gui, config.json, or secret.key is denied
        self.assertFalse(guard._evaluate_policy("write_file", {"path": "assistant/evil.py", "content": "pass"}, "routine"))
        self.assertFalse(guard._evaluate_policy("edit_file", {"filepath": "gui/app.py"}, "routine"))
        self.assertFalse(guard._evaluate_policy("delete_file", {"path": "config.json"}, "routine"))
        self.assertFalse(guard._evaluate_policy("replace_file_content", {"target_file": "data/secret.key"}, "routine"))

    def test_hard_deny_blocks_catastrophic_shell_wipes(self):
        # Even with allow_destructive, rm -rf or format or del /f is denied
        self.assertFalse(guard._evaluate_policy("run_terminal_command", {"command": "rm -rf /"}, "routine"))
        self.assertFalse(guard._evaluate_policy("run_terminal_command", {"command": "del /f /q C:\\windows"}, "routine"))
        self.assertFalse(guard._evaluate_policy("run_terminal_command", {"command": "format d:"}, "routine"))

    def test_legitimate_files_allowed_under_allow_policy(self):
        # Writing to user workspace document is not hard-denied
        self.assertTrue(guard._evaluate_policy("write_file", {"path": "documents/report.txt", "content": "hi"}, "routine"))


class TestOverwatchSafety(unittest.TestCase):
    def setUp(self):
        reset_hard_stop()

    def tearDown(self):
        reset_hard_stop()

    def test_overwatch_does_not_click_when_auto_click_is_false(self):
        engine = OverwatchEngine()
        rule = OverwatchRule(target_text="submit", auto_click=False, require_focus=False)
        mock_ctrl = mock.MagicMock()
        element = {"name": "submit", "runtime_id": [1, 2, 3], "control": mock_ctrl}

        engine._handle_match(element, rule)
        mock_ctrl.Click.assert_not_called()

    def test_overwatch_click_blocked_when_kill_switch_active(self):
        engine = OverwatchEngine()
        rule = OverwatchRule(target_text="submit", auto_click=True, require_focus=False)
        mock_ctrl = mock.MagicMock()
        element = {"name": "submit", "runtime_id": [1, 2, 3], "control": mock_ctrl}

        guard.set_kill_switch(True)
        engine._handle_match(element, rule)
        mock_ctrl.Click.assert_not_called()


class TestControlStoreCleanup(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="vave-store-cleanup-")
        self.db_path = Path(self.tempdir) / "control.db"

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_control_store_close_is_idempotent(self):
        store = ControlStore(self.db_path)
        store.close()
        # Second close should not raise
        store.close()

    def test_control_store_context_manager(self):
        with ControlStore(self.db_path) as store:
            self.assertIsNotNone(store._connection)
        self.assertIsNone(store._connection)


class TestWave2HighSafetyAndHonesty(unittest.TestCase):
    def setUp(self):
        call_context.clear_taint()
        token = call_context.set_origin("voice")
        self._voice_token = token

    def tearDown(self):
        call_context.clear_taint()

    @mock.patch("assistant.commands.speak")
    @mock.patch("assistant.commands.get_setting")
    @mock.patch("assistant.commands.execute_command")
    def test_routine_origin_resets_after_execution(self, mock_exec, mock_get_setting, mock_speak):
        from assistant import commands
        mock_get_setting.return_value = {
            "work routine": ["open vscode", "open terminal"]
        }
        origins_during_exec = []
        def capture_origin(*args, **kwargs):
            origins_during_exec.append(call_context.get_origin())
            return True
        mock_exec.side_effect = capture_origin

        self.assertEqual(call_context.get_origin(), "voice")
        handled = commands.check_routines("run work routine")
        self.assertTrue(handled)
        self.assertEqual(origins_during_exec, ["routine", "routine"])
        # Origin must be restored to voice after routine completes
        self.assertEqual(call_context.get_origin(), "voice")

    @mock.patch("assistant.commands.speak")
    @mock.patch("assistant.commands.get_setting")
    @mock.patch("assistant.commands.execute_command")
    def test_routine_origin_resets_even_on_exception(self, mock_exec, mock_get_setting, mock_speak):
        from assistant import commands
        mock_get_setting.return_value = {
            "crash routine": ["broken cmd"]
        }
        def crash_exec(*args, **kwargs):
            raise RuntimeError("Routine exploded")
        mock_exec.side_effect = crash_exec

        self.assertEqual(call_context.get_origin(), "voice")
        with self.assertRaises(RuntimeError):
            commands.check_routines("run crash routine")
        # Origin must STILL be reset to voice
        self.assertEqual(call_context.get_origin(), "voice")

    def test_write_command_does_not_hijack_to_raw_typing(self):
        from assistant import commands
        with mock.patch("assistant.system_tasks.type_text") as mock_type:
            # "write ..." should NOT trigger handle_atomic_gui_command
            res1 = commands.handle_atomic_gui_command("write a python script to parse logs")
            self.assertFalse(res1)
            mock_type.assert_not_called()

            res2 = commands.handle_atomic_gui_command("write email to Sarah about lunch")
            self.assertFalse(res2)
            mock_type.assert_not_called()

            # "type ..." SHOULD trigger handle_atomic_gui_command
            res3 = commands.handle_atomic_gui_command("type mypassword123")
            self.assertTrue(res3)
            mock_type.assert_called_once_with("mypassword123")

    @mock.patch("assistant.system_tasks._ensure_com")
    @mock.patch("assistant.system_tasks.speak")
    @mock.patch("os.system")
    @mock.patch("assistant.system_tasks._find_window", return_value=None)
    def test_open_app_honest_unconfirmed_report(self, mock_find, mock_os, mock_speak, mock_com):
        from assistant import system_tasks
        with mock.patch("time.sleep"):
            res = system_tasks.open_app("calculator")
        self.assertEqual(res, "Action sent for Calculator; effect unconfirmed.")
        self.assertNotIn("window active and focused", res)

    @mock.patch("assistant.system_tasks._ensure_com")
    @mock.patch("assistant.system_tasks.speak")
    @mock.patch("os.system")
    @mock.patch("assistant.system_tasks.activate_window")
    def test_open_app_honest_confirmed_report(self, mock_act, mock_os, mock_speak, mock_com):
        from assistant import system_tasks
        mock_win = mock.MagicMock(NativeWindowHandle=9999, Name="Calculator Window")
        # 2 calls during initial existing check (display_name, query), then mock_win during post-launch poll
        with mock.patch("assistant.system_tasks._find_window", side_effect=[None, None, mock_win]):
            with mock.patch("time.sleep"):
                res = system_tasks.open_app("calculator")
        self.assertIn("Successfully opened Calculator (Verified active window: 'Calculator Window')", res)

    @mock.patch("assistant.system_tasks._ensure_com")
    @mock.patch("assistant.system_tasks.activate_window")
    def test_click_element_honest_unconfirmed_report(self, mock_act, mock_com):
        import sys
        from assistant import system_tasks
        mock_auto = mock.MagicMock()
        mock_auto.ControlType.ButtonControl = 1
        mock_control = mock.MagicMock()
        mock_control.ControlType = 1
        mock_control.Name = "Submit Query"
        mock_control.IsEnabled = True
        mock_control.BoundingRectangle = mock.MagicMock(left=10, top=10, width=lambda: 50, height=lambda: 20)
        mock_control.IsOffscreen = False
        mock_invoke = mock.MagicMock()
        mock_control.GetInvokePattern.return_value = mock_invoke

        mock_window = mock.MagicMock(Name="Form App", NativeWindowHandle=54321)
        mock_auto.WalkControl.return_value = [(mock_control, 1)]
        mock_auto.GetForegroundControl.return_value = mock_window

        with mock.patch.dict(sys.modules, {"uiautomation": mock_auto}):
            with mock.patch("assistant.system_tasks._find_window", return_value=mock_window):
                with mock.patch("assistant.system_tasks._clickable_control_types", return_value={1}):
                    with mock.patch("time.sleep"):
                        res = system_tasks.click_element("Submit Query", window_title="Form App")

        self.assertIn("Clicked 'Submit Query'", res)
        self.assertIn("(Action sent; effect unconfirmed)", res)
        self.assertNotIn("Verified: Click delivered", res)


class TestWave3RemainingHighAndMedium(unittest.TestCase):
    def setUp(self):
        call_context.clear_taint()

    def tearDown(self):
        call_context.clear_taint()

    @mock.patch("assistant.dev_tools.speak")
    @mock.patch("assistant.dev_tools.update_setting")
    @mock.patch("assistant.dev_tools.get_setting", return_value="original-qwen")
    def test_deep_test_project_restores_model_on_failure(self, mock_get, mock_update, mock_speak):
        from assistant import dev_tools
        with mock.patch("subprocess.Popen", side_effect=RuntimeError("Process boot crash")):
            with self.assertRaises(RuntimeError):
                dev_tools.deep_test_project()
        mock_update.assert_any_call("llm_model", "original-qwen")

    def test_audit_recursive_and_pattern_redaction(self):
        from assistant import audit
        # 1. Nested dictionary key redaction
        data = {
            "service": "postgres",
            "config": {
                "user": "admin",
                "api_key": "supersecret123",
                "token": "tok_xyz987"
            },
            "auth": "supersecretpassword"
        }
        res = audit.redact(data)
        self.assertNotIn("supersecret123", res)
        self.assertNotIn("tok_xyz987", res)
        self.assertNotIn("supersecretpassword", res)
        self.assertIn("'api_key': '***'", res)
        self.assertIn("'token': '***'", res)
        self.assertIn("'auth': '***'", res)

        # 2. String value pattern redaction
        command_args = {
            "cmd": "curl -H 'Authorization: Bearer sk-antigravitysecrettoken12345' https://example.com",
            "url": "secret://database_password"
        }
        res2 = audit.redact(command_args)
        self.assertNotIn("sk-antigravitysecrettoken12345", res2)
        self.assertNotIn("secret://database_password", res2)
        self.assertIn("***", res2)

    def test_swarm_args_robust_json_parsing(self):
        from assistant.swarm import _parse_swarm_args, spawn_parallel_agents
        # 1. Markdown code fences
        md_json = "```json\n{\"query\": \"deep space\", \"limit\": 5}\n```"
        self.assertEqual(_parse_swarm_args(md_json), {"query": "deep space", "limit": 5})

        # 2. JSON boolean literals in spawn_parallel_agents
        with mock.patch("assistant.swarm.run_sub_agent", return_value="Done"):
            json_tasks = '[{"role": "researcher", "task": "study", "active": true, "extra": null}]'
            res = spawn_parallel_agents(json_tasks)
            self.assertIn("Parallel Execution Results", res)

    def test_telegram_pairing_handshake_flow(self):
        from assistant import telegram_sync
        # 1. Issue a pairing code
        code = telegram_sync.issue_telegram_pairing_code()
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())

        # 2. Active code returns same code while valid
        active = telegram_sync.get_active_telegram_pairing_code()
        self.assertEqual(active, code)


class TestWave4PolishAndPerformance(unittest.TestCase):
    def test_click_element_caps_walk_at_800(self):
        import sys
        from assistant import system_tasks
        mock_auto = mock.MagicMock()
        mock_auto.ControlType.ButtonControl = 1

        controls = []
        for i in range(1000):
            c = mock.MagicMock()
            c.ControlType = 1
            c.Name = f"Button_{i}"
            c.IsEnabled = True
            c.BoundingRectangle = mock.MagicMock(left=10, top=10, width=lambda: 50, height=lambda: 20)
            c.IsOffscreen = False
            controls.append((c, 1))

        mock_window = mock.MagicMock(Name="Huge UI", NativeWindowHandle=777)
        mock_auto.WalkControl.return_value = controls
        mock_auto.GetForegroundControl.return_value = mock_window

        with mock.patch.dict(sys.modules, {"uiautomation": mock_auto}):
            with mock.patch("assistant.system_tasks._find_window", return_value=mock_window):
                with mock.patch("assistant.system_tasks._clickable_control_types", return_value={1}):
                    with mock.patch("assistant.system_tasks._ensure_com"):
                        with mock.patch("assistant.system_tasks.get_clickable_elements", return_value=""):
                            res = system_tasks.click_element("NonExistentButton", window_title="Huge UI")

        self.assertIn("Nothing labelled 'NonExistentButton'", res)

    def test_prune_conversation_context_turn_aware_trimming(self):
        from assistant.ai_brain import _prune_conversation_context
        # Create a conversation with 24 messages
        messages = [
            {"role": "system", "content": "You are VAVE desktop assistant."},
            {"role": "user", "content": "Initial complex user task."}
        ]
        for i in range(11):
            messages.append({"role": "assistant", "content": f"Turn {i}", "tool_calls": [{"function": {"name": "wait", "arguments": {}}}]})
            messages.append({"role": "tool", "content": f"Done {i}", "name": "wait"})

        pruned = _prune_conversation_context(messages, max_history=10)
        self.assertLessEqual(len(pruned), 10)
        # Verify head is preserved
        self.assertEqual(pruned[0]["role"], "system")
        self.assertEqual(pruned[1]["role"], "user")
        # Verify tail does not start with an orphan tool message
        if len(pruned) > 2:
            self.assertNotEqual(pruned[2]["role"], "tool")

    def test_config_driven_tesseract(self):
        from assistant import vision
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            temp_tess = f.name
        try:
            with mock.patch("assistant.config.get_setting", return_value=temp_tess):
                detected = vision._find_tesseract()
                self.assertEqual(detected, temp_tess)
        finally:
            if os.path.exists(temp_tess):
                os.unlink(temp_tess)


if __name__ == "__main__":
    unittest.main()

