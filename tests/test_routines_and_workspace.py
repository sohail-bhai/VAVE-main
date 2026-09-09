"""Unit tests for Zero-Latency Routines, Split Workspace, and Deep XAML Traverser."""

import unittest
from unittest import mock

from assistant import system_tasks
from assistant import commands
from assistant import ai_brain
from assistant.control.capabilities import TOOL_CAPABILITIES


class SplitWorkspaceTests(unittest.TestCase):
    """Tests for organize_workspace tool and window split logic."""

    def test_organize_workspace_requires_both_apps(self):
        res1 = system_tasks.organize_workspace("", "calculator")
        self.assertIn("specify both", res1.lower())

        res2 = system_tasks.organize_workspace("code", "")
        self.assertIn("specify both", res2.lower())

    @mock.patch("assistant.system_tasks.get_os", return_value="windows")
    @mock.patch("assistant.system_tasks._find_window")
    @mock.patch("assistant.system_tasks.open_app")
    @mock.patch("assistant.system_tasks.snap_window")
    def test_organize_workspace_launches_and_snaps(self, mock_snap, mock_open, mock_find, mock_get_os):
        # Pretend neither app is open initially, then open after open_app
        mock_win_left = mock.MagicMock(Name="Visual Studio Code")
        mock_win_right = mock.MagicMock(Name="Command Prompt")
        mock_find.side_effect = [None, mock_win_left, None, mock_win_right, mock_win_left, mock_win_right]

        mock_snap.side_effect = ["Snapped left", "Snapped right"]

        res = system_tasks.organize_workspace("code", "cmd")

        self.assertEqual(mock_open.call_count, 2)
        mock_snap.assert_any_call("code", "left")
        mock_snap.assert_any_call("cmd", "right")
        self.assertIn("Visual Studio Code", res)
        self.assertIn("Command Prompt", res)
        self.assertIn("snapped to left half", res)
        self.assertIn("snapped to right half", res)


class CommandRoutingRoutinesAndSplitTests(unittest.TestCase):
    """Tests fast-path routing for split screen and config routines."""

    @mock.patch("assistant.system_tasks.organize_workspace", return_value="Workspace organized.")
    @mock.patch("assistant.commands.speak")
    def test_split_screen_commands(self, mock_speak, mock_org):
        test_phrases = [
            "split screen notepad and calculator",
            "organize workspace code and cmd",
            "tile vscode and browser",
            "put notepad on left and calculator on right",
        ]
        for phrase in test_phrases:
            with self.subTest(phrase=phrase):
                handled = commands.handle_workspace_split_command(phrase)
                self.assertTrue(handled, f"Failed to route split phrase: '{phrase}'")

        self.assertEqual(mock_org.call_count, len(test_phrases))

    @mock.patch("assistant.commands.execute_command", return_value=True)
    @mock.patch("assistant.commands.speak")
    @mock.patch("assistant.commands.get_setting")
    def test_check_routines_execution(self, mock_get_setting, mock_speak, mock_exec):
        mock_get_setting.return_value = {
            "dev mode": ["split screen code and cmd", "set volume to 30"],
            "focus mode": ["set volume to 20", "open chatgpt"]
        }

        # Exact match
        handled = commands.check_routines("dev mode")
        self.assertTrue(handled)
        mock_exec.assert_any_call("split screen code and cmd", auto_confirm=True)
        mock_exec.assert_any_call("set volume to 30", auto_confirm=True)

        # Prefixed match
        handled2 = commands.check_routines("run focus mode")
        self.assertTrue(handled2)
        mock_exec.assert_any_call("set volume to 20", auto_confirm=True)
        mock_exec.assert_any_call("open chatgpt", auto_confirm=True)

        # Unrelated command
        handled_none = commands.check_routines("what is the weather today")
        self.assertFalse(handled_none)


class DeepXamlTraverserTests(unittest.TestCase):
    """Tests for UWP/WinUI expanded control types and direct InvokePattern."""

    def test_clickable_control_types_includes_xaml_containers(self):
        mock_auto = mock.MagicMock()
        mock_auto.ControlType.ButtonControl = 1
        mock_auto.ControlType.MenuItemControl = 2
        mock_auto.ControlType.TabItemControl = 3
        mock_auto.ControlType.ListItemControl = 4
        mock_auto.ControlType.HyperlinkControl = 5
        mock_auto.ControlType.EditControl = 6
        mock_auto.ControlType.CheckBoxControl = 7
        mock_auto.ControlType.RadioButtonControl = 8
        mock_auto.ControlType.ComboBoxControl = 9
        mock_auto.ControlType.TreeItemControl = 10
        mock_auto.ControlType.SliderControl = 11
        mock_auto.ControlType.CustomControl = 12
        mock_auto.ControlType.ImageControl = 13
        mock_auto.ControlType.GroupControl = 14
        mock_auto.ControlType.PaneControl = 15

        types = system_tasks._clickable_control_types(mock_auto)
        self.assertIn(mock_auto.ControlType.CustomControl, types)
        self.assertIn(mock_auto.ControlType.ImageControl, types)
        self.assertIn(mock_auto.ControlType.GroupControl, types)
        self.assertIn(mock_auto.ControlType.PaneControl, types)

    @mock.patch("assistant.system_tasks._ensure_com")
    @mock.patch("assistant.system_tasks.activate_window")
    def test_click_element_prefers_invoke_pattern(self, mock_act, mock_com):
        import sys
        mock_auto = mock.MagicMock()
        mock_auto.ControlType.ButtonControl = 1
        mock_control = mock.MagicMock()
        mock_control.ControlType = 1
        mock_control.Name = "Action Movie 1"
        mock_control.IsEnabled = True
        mock_control.BoundingRectangle = mock.MagicMock(left=100, top=100, width=lambda: 200, height=lambda: 100)
        mock_control.IsOffscreen = False
        mock_invoke = mock.MagicMock()
        mock_control.GetInvokePattern.return_value = mock_invoke

        mock_window = mock.MagicMock(Name="Netflix", NativeWindowHandle=12345)
        mock_auto.WalkControl.return_value = [(mock_control, 1)]

        with mock.patch.dict(sys.modules, {"uiautomation": mock_auto}):
            with mock.patch("assistant.system_tasks._find_window", return_value=mock_window):
                with mock.patch("assistant.system_tasks._clickable_control_types", return_value={1}):
                    res = system_tasks.click_element("Action Movie 1", window_title="Netflix")

        mock_invoke.Invoke.assert_called_once()
        self.assertIn("Clicked 'Action Movie 1'", res)


class BrainRegistrationAndCapabilitiesTests(unittest.TestCase):
    """Tests function registration and capability mapping for organize_workspace."""

    def test_organize_workspace_in_available_functions(self):
        self.assertIn("organize_workspace", ai_brain.AVAILABLE_FUNCTIONS)
        self.assertEqual(ai_brain.AVAILABLE_FUNCTIONS["organize_workspace"], system_tasks.organize_workspace)

    def test_organize_workspace_in_capabilities(self):
        self.assertIn("organize_workspace", TOOL_CAPABILITIES)
        self.assertEqual(TOOL_CAPABILITIES["organize_workspace"], "system.window.manage")

    def test_organize_workspace_tool_selection(self):
        tools = [t["function"]["name"] for t in ai_brain.select_tools("organize workspace between code and terminal")]
        self.assertIn("organize_workspace", tools)
        self.assertIn("snap_window", tools)


if __name__ == "__main__":
    unittest.main()
