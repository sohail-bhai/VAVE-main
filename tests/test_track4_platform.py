"""Tests for Track 4: Linux/macOS parity for system tasks.

Everything runs mocked: get_os() is faked per test, helper binaries are
faked, and no window is ever touched. Nothing here depends on the machine
running the suite.
"""

import unittest
from unittest import mock


class PlatformHelperTests(unittest.TestCase):
    def setUp(self):
        from assistant import system_tasks
        system_tasks._TOOL_CACHE.clear()
        self.addCleanup(system_tasks._TOOL_CACHE.clear)

    def test_have_tool_caches_lookups(self):
        from assistant import system_tasks
        with mock.patch("shutil.which", return_value="/usr/bin/x") as mock_which:
            self.assertTrue(system_tasks._have_tool("wmctrl"))
            self.assertTrue(system_tasks._have_tool("wmctrl"))
        mock_which.assert_called_once_with("wmctrl")

    def test_run_capture_never_raises(self):
        from assistant import system_tasks
        with mock.patch("subprocess.run", side_effect=FileNotFoundError()):
            self.assertEqual((127, ""), system_tasks._run_capture(["nope"]))
        with mock.patch("subprocess.run", side_effect=TimeoutError()):
            self.assertEqual((1, ""), system_tasks._run_capture(["slow"]))

    def test_applescript_quoting_escapes(self):
        from assistant import system_tasks
        self.assertEqual('say \\"hi\\" \\\\ done',
                         system_tasks._as_quote('say "hi" \\ done'))


class ListWindowsTests(unittest.TestCase):
    def setUp(self):
        from assistant import system_tasks
        system_tasks._TOOL_CACHE.clear()
        self.addCleanup(system_tasks._TOOL_CACHE.clear)

    def test_macos_lists_applescript_windows(self):
        from assistant import system_tasks
        names = "Safari, Terminal, Calendar"
        with mock.patch("assistant.system_tasks.get_os", return_value="darwin"), \
             mock.patch("assistant.system_tasks._osascript",
                        side_effect=[(0, names), (0, "Terminal")]):
            result = system_tasks.list_windows()
        self.assertIn("- 'Safari'", result)
        self.assertIn("- 'Terminal'  <- currently in focus", result)

    def test_macos_reports_permission_problem(self):
        from assistant import system_tasks
        with mock.patch("assistant.system_tasks.get_os", return_value="darwin"), \
             mock.patch("assistant.system_tasks._osascript",
                        return_value=(1, "")):
            result = system_tasks.list_windows()
        self.assertIn("Accessibility", result)

    def test_linux_parses_wmctrl(self):
        from assistant import system_tasks
        out = ("0x01 0 host One App\n"
               "0x02 1 host Two App\n")
        with mock.patch("assistant.system_tasks.get_os", return_value="linux"), \
             mock.patch("assistant.system_tasks._have_tool",
                        side_effect=lambda name: name == "wmctrl"), \
             mock.patch("assistant.system_tasks._run_capture",
                        return_value=(0, out)):
            result = system_tasks.list_windows()
        self.assertIn("- 'One App'", result)
        self.assertIn("- 'Two App'", result)

    def test_linux_without_tools_explains_itself(self):
        from assistant import system_tasks
        with mock.patch("assistant.system_tasks.get_os", return_value="linux"), \
             mock.patch("assistant.system_tasks._have_tool",
                        return_value=False):
            result = system_tasks.list_windows()
        self.assertIn("wmctrl", result)


class FocusCloseTests(unittest.TestCase):
    def test_macos_focus_escapes_title(self):
        from assistant import system_tasks
        with mock.patch("assistant.system_tasks.get_os", return_value="darwin"), \
             mock.patch("assistant.system_tasks._osascript",
                        return_value=(0, "")) as mock_osa:
            result = system_tasks.focus_window('Say "hi"')
        self.assertIn("Focused", result)
        first_script = mock_osa.call_args_list[0][0][0]
        self.assertIn('\\"hi\\"', first_script)

    def test_linux_focus_prefers_wmctrl(self):
        from assistant import system_tasks
        with mock.patch("assistant.system_tasks.get_os", return_value="linux"), \
             mock.patch("assistant.system_tasks._have_tool",
                        side_effect=lambda name: True), \
             mock.patch("assistant.system_tasks._run_capture",
                        return_value=(0, "")) as mock_run:
            result = system_tasks.focus_window("Terminal")
        self.assertIn("Terminal", result)
        self.assertEqual(["wmctrl", "-a", "Terminal"],
                         mock_run.call_args[0][0])

    def test_linux_close_uses_windowclose(self):
        from assistant import system_tasks
        with mock.patch("assistant.system_tasks.get_os", return_value="linux"), \
             mock.patch("assistant.system_tasks._have_tool",
                        side_effect=lambda name: name == "xdotool"), \
             mock.patch("assistant.system_tasks._run_capture",
                        return_value=(0, "")) as mock_run:
            result = system_tasks.close_window("Terminal")
        self.assertIn("Closed", result)
        args = mock_run.call_args[0][0]
        self.assertIn("windowclose", args)

    def test_macos_close_missing_window_reports(self):
        from assistant import system_tasks
        with mock.patch("assistant.system_tasks.get_os", return_value="darwin"), \
             mock.patch("assistant.system_tasks._osascript",
                        return_value=(1, "")):
            result = system_tasks.close_window("Nope")
        self.assertIn("No open window matches", result)


class OpenAppAliasesTests(unittest.TestCase):
    def test_macos_safari_launches(self):
        from assistant import system_tasks
        with mock.patch("assistant.system_tasks.get_os", return_value="darwin"), \
             mock.patch("assistant.system_tasks.speak"), \
             mock.patch("os.system") as mock_os:
            result = system_tasks.open_app("safari")
        mock_os.assert_called_once_with("open -a Safari")
        self.assertIn("Successfully opened", result)

    def test_linux_firefox_launches(self):
        from assistant import system_tasks
        with mock.patch("assistant.system_tasks.get_os", return_value="linux"), \
             mock.patch("assistant.system_tasks.speak"), \
             mock.patch("subprocess.Popen") as mock_popen:
            result = system_tasks.open_app("firefox")
        mock_popen.assert_called_once_with("firefox", shell=True)
        self.assertIn("Successfully opened", result)


class NotifyTests(unittest.TestCase):
    def test_macos_notification_command(self):
        from assistant import system_tasks
        with mock.patch("assistant.system_tasks.get_os", return_value="darwin"), \
             mock.patch("assistant.system_tasks._osascript",
                        return_value=(0, "")) as mock_osa:
            result = system_tasks.notify_user("Hi", "there")
        self.assertIn("Showed notification", result)
        self.assertIn("display notification", mock_osa.call_args[0][0])

    def test_linux_notification_command(self):
        from assistant import system_tasks
        with mock.patch("assistant.system_tasks.get_os", return_value="linux"), \
             mock.patch("assistant.system_tasks._have_tool",
                        return_value=True), \
             mock.patch("assistant.system_tasks._run_capture",
                        return_value=(0, "")) as mock_run:
            result = system_tasks.notify_user("Hi", "there")
        self.assertIn("Showed notification", result)
        self.assertEqual(["notify-send", "Hi", "there"],
                         mock_run.call_args[0][0])

    def test_linux_without_notify_send_explains(self):
        from assistant import system_tasks
        with mock.patch("assistant.system_tasks.get_os", return_value="linux"), \
             mock.patch("assistant.system_tasks._have_tool",
                        return_value=False):
            result = system_tasks.notify_user("Hi", "there")
        self.assertIn("libnotify", result)

    def test_windows_toast_success_and_fallback(self):
        from assistant import system_tasks
        with mock.patch("assistant.system_tasks.get_os", return_value="windows"), \
             mock.patch("assistant.system_tasks._notify_windows",
                        return_value=True):
            self.assertIn("Showed notification",
                          system_tasks.notify_user("Hi", "there"))
        with mock.patch("assistant.system_tasks.get_os", return_value="windows"), \
             mock.patch("assistant.system_tasks._notify_windows",
                        return_value=False):
            self.assertIn("logged instead",
                          system_tasks.notify_user("Hi", "there"))


if __name__ == "__main__":
    unittest.main()
