"""Unit tests for Global Media Controller, Native Window Snapping, and Closed-Loop Verification."""

import unittest
from unittest.mock import patch, MagicMock


class MediaControlTests(unittest.TestCase):
    """Checks the media_control hardware key dispatcher."""

    @patch("assistant.system_tasks.get_os", return_value="windows")
    @patch("ctypes.windll.user32.keybd_event")
    def test_media_actions_windows(self, mock_keybd, mock_os):
        from assistant.system_tasks import media_control

        # Action: play_pause
        res = media_control("play_pause")
        self.assertIn("success", res.lower())
        # VK_MEDIA_PLAY_PAUSE is 0xB3 (179)
        mock_keybd.assert_any_call(0xB3, 0, 1, 0)

        mock_keybd.reset_mock()
        # Action: next
        res = media_control("next")
        self.assertIn("success", res.lower())
        # VK_MEDIA_NEXT_TRACK is 0xB0 (176)
        mock_keybd.assert_any_call(0xB0, 0, 1, 0)

        mock_keybd.reset_mock()
        # Action: previous
        res = media_control("previous")
        self.assertIn("success", res.lower())
        # VK_MEDIA_PREV_TRACK is 0xB1 (177)
        mock_keybd.assert_any_call(0xB1, 0, 1, 0)

        mock_keybd.reset_mock()
        # Action: stop
        res = media_control("stop")
        self.assertIn("success", res.lower())
        # VK_MEDIA_STOP is 0xB2 (178)
        mock_keybd.assert_any_call(0xB2, 0, 1, 0)

        mock_keybd.reset_mock()
        # Action: mute
        res = media_control("mute")
        self.assertIn("success", res.lower())
        # VK_VOLUME_MUTE is 0xAD (173)
        mock_keybd.assert_any_call(0xAD, 0, 1, 0)

    @patch("assistant.system_tasks.get_os", return_value="windows")
    def test_unknown_action(self, mock_os):
        from assistant.system_tasks import media_control
        res = media_control("unknown_action_xyz")
        self.assertIn("unknown media action", res.lower())


class SnapWindowTests(unittest.TestCase):
    """Checks window snapping and positioning."""

    @patch("assistant.system_tasks.get_os", return_value="windows")
    @patch("assistant.system_tasks._find_window")
    @patch("ctypes.windll.user32.SetWindowPos")
    @patch("ctypes.windll.user32.ShowWindow")
    @patch("ctypes.windll.user32.SystemParametersInfoW")
    def test_snap_window_left_and_right(self, mock_spi, mock_show, mock_set_pos, mock_find, mock_os):
        from assistant.system_tasks import snap_window

        # Setup mock window
        mock_win = MagicMock()
        mock_win.Name = "Test App"
        mock_win.NativeWindowHandle = 12345
        mock_find.return_value = mock_win

        # Mock work area 1920x1080 (0,0 to 1920, 1040 excluding 40px taskbar)
        def fake_spi(action, ui_param, pv_param, f_win_ini):
            rect = pv_param._obj
            rect.left = 0
            rect.top = 0
            rect.right = 1920
            rect.bottom = 1040
            return 1

        mock_spi.side_effect = fake_spi

        # Snap left
        res = snap_window("Test App", "left")
        self.assertIn("snapped", res.lower())
        self.assertIn("left half", res.lower())
        # HWND 12345, x=0, y=0, w=960, h=1040
        mock_set_pos.assert_called_with(12345, 0, 0, 0, 960, 1040, 0x0040)

        mock_set_pos.reset_mock()
        # Snap right
        res = snap_window("Test App", "right")
        self.assertIn("snapped", res.lower())
        self.assertIn("right half", res.lower())
        # HWND 12345, x=960, y=0, w=960, h=1040
        mock_set_pos.assert_called_with(12345, 0, 960, 0, 960, 1040, 0x0040)

    @patch("assistant.system_tasks.get_os", return_value="windows")
    @patch("assistant.system_tasks._find_window", return_value=None)
    def test_snap_window_not_found(self, mock_find, mock_os):
        from assistant.system_tasks import snap_window
        res = snap_window("NonexistentApp", "left")
        self.assertIn("could not find", res.lower())


class CommandRoutingMediaAndWindowTests(unittest.TestCase):
    """Verifies fast-path voice command routing for media and snapping."""

    @patch("assistant.commands.speak")
    @patch("assistant.system_tasks.media_control")
    def test_media_voice_commands(self, mock_media, mock_speak):
        from assistant.commands import execute_single_command

        for phrase, action in [
            ("pause music", "play_pause"),
            ("resume playback", "play_pause"),
            ("play music", "play_pause"),
            ("next song", "next"),
            ("skip track", "next"),
            ("previous song", "previous"),
            ("stop music", "stop"),
            ("mute audio", "mute"),
        ]:
            with self.subTest(phrase=phrase):
                mock_media.reset_mock()
                mock_media.return_value = f"Media command '{action}' executed successfully."
                handled = execute_single_command(phrase)
                self.assertTrue(handled, f"Expected {phrase!r} to be handled")
                mock_media.assert_called_with(action)

    @patch("assistant.commands.speak")
    @patch("assistant.system_tasks.snap_window")
    def test_snap_voice_commands(self, mock_snap, mock_speak):
        from assistant.commands import execute_single_command

        mock_snap.return_value = "Snapped 'Notepad' to left half."
        handled = execute_single_command("snap notepad to left")
        self.assertTrue(handled)
        mock_snap.assert_called_with("notepad", "left")

        mock_snap.reset_mock()
        mock_snap.return_value = "Snapped 'Netflix' to right half."
        handled = execute_single_command("put netflix on the right")
        self.assertTrue(handled)
        mock_snap.assert_called_with("netflix", "right")

        mock_snap.reset_mock()
        mock_snap.return_value = "Maximized 'Chrome'."
        handled = execute_single_command("maximize chrome")
        self.assertTrue(handled)
        mock_snap.assert_called_with("chrome", "maximize")


class BrainToolsAndCapabilitiesTests(unittest.TestCase):
    """Verifies tools are registered in ai_brain and capabilities catalog."""

    def test_tools_registered_in_brain(self):
        from assistant.ai_brain import AVAILABLE_FUNCTIONS, LLM_TOOLS

        self.assertIn("media_control", AVAILABLE_FUNCTIONS)
        self.assertIn("snap_window", AVAILABLE_FUNCTIONS)

        schema_names = [s["function"]["name"] for s in LLM_TOOLS if "function" in s]
        self.assertIn("media_control", schema_names)
        self.assertIn("snap_window", schema_names)

    def test_capabilities_catalog(self):
        from assistant.control.capabilities import TOOL_CAPABILITIES, CATALOG

        self.assertIn("media_control", TOOL_CAPABILITIES)
        self.assertIn("snap_window", TOOL_CAPABILITIES)
        self.assertIn("system.action", CATALOG)
        self.assertIn("system.window.manage", CATALOG)


if __name__ == "__main__":
    unittest.main()
