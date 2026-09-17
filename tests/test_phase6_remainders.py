"""Tests for the Phase 6 remainders (plan.md section 8).

6.1 open_app reports a failed launch honestly (return-code check) and
    close_app reports AccessDenied instead of swallowing it.
6.3 The bare `time` alternative is gone from _TIME_PATTERN.
6.4 process_command names the failing command in its spoken error.
"""

import unittest
from unittest import mock


class OpenAppReturnCodeTests(unittest.TestCase):
    def _open(self, query, rc):
        from assistant import system_tasks
        completed = mock.MagicMock(returncode=rc)
        with mock.patch("assistant.system_tasks._ensure_com"), \
             mock.patch("assistant.system_tasks.speak"), \
             mock.patch("assistant.system_tasks._find_window",
                        return_value=None), \
             mock.patch("subprocess.run",
                        return_value=completed) as mock_run, \
             mock.patch("time.sleep"):
            result = system_tasks.open_app(query)
        return result, mock_run

    def test_nonzero_exit_reports_failure(self):
        result, mock_run = self._open("calculator", 1)
        mock_run.assert_called_once()
        argv = mock_run.call_args[0][0]
        self.assertEqual(argv[:2], ["cmd", "/c"])
        self.assertIn("Could not open Calculator", result)
        self.assertIn("code 1", result)

    def test_zero_exit_without_window_stays_unconfirmed(self):
        result, _mock_run = self._open("calculator", 0)
        self.assertIn("effect unconfirmed", result)

    def test_success_path_unchanged(self):
        from assistant import system_tasks
        mock_win = mock.MagicMock(NativeWindowHandle=9999, Name="Calc")
        completed = mock.MagicMock(returncode=0)
        # First two lookups are the already-open pre-check (nothing yet),
        # the third is the post-launch poll finding the new window.
        with mock.patch("assistant.system_tasks._ensure_com"), \
             mock.patch("assistant.system_tasks.speak"), \
             mock.patch("assistant.system_tasks.activate_window"), \
             mock.patch("assistant.system_tasks._find_window",
                        side_effect=[None, None, mock_win]), \
             mock.patch("subprocess.run",
                        return_value=completed), \
             mock.patch("time.sleep"):
            result = system_tasks.open_app("calculator")
        self.assertIn("Successfully opened", result)


class CloseAppAccessDeniedTests(unittest.TestCase):
    def test_access_denied_is_reported(self):
        import psutil
        from assistant import system_tasks
        proc = mock.MagicMock()
        proc.info = {"name": "notepad.exe", "pid": 4242}
        proc.terminate.side_effect = psutil.AccessDenied(4242, "notepad.exe")
        with mock.patch("psutil.process_iter", return_value=[proc]), \
             mock.patch("assistant.system_tasks.close_window",
                        return_value="No window."), \
             mock.patch("assistant.system_tasks.speak"):
            result = system_tasks.close_app("notepad")
        self.assertIn("access denied", result)
        self.assertIn("administrator", result)

    def test_missing_app_still_reports_not_found(self):
        from assistant import system_tasks
        with mock.patch("psutil.process_iter", return_value=[]), \
             mock.patch("assistant.system_tasks.close_window",
                        return_value="No window."), \
             mock.patch("assistant.system_tasks.speak"):
            result = system_tasks.close_app("notepad")
        self.assertIn("No running process found", result)


class TimePatternTests(unittest.TestCase):
    def test_bare_time_no_longer_matches(self):
        from assistant.commands import _TIME_PATTERN
        self.assertIsNone(_TIME_PATTERN.match("time"))

    def test_shutdown_time_goes_to_ai_brain(self):
        from assistant.commands import _TIME_PATTERN
        self.assertIsNone(_TIME_PATTERN.match("shutdown time"))

    def test_real_time_requests_still_match(self):
        from assistant.commands import _TIME_PATTERN
        for text in ("what time is it", "tell me the time",
                     "what's the time", "current time"):
            with self.subTest(text=text):
                self.assertIsNotNone(_TIME_PATTERN.match(text))


class ProcessCommandErrorTests(unittest.TestCase):
    def test_error_speech_names_the_command(self):
        from unittest.mock import patch
        from assistant.controller import AssistantController
        controller = AssistantController(
            configure_speech_hooks=False,
            speech_enabled=False,
        )
        with patch("assistant.controller.execute_command",
                   side_effect=RuntimeError("boom")), \
             patch("assistant.controller.speak") as mock_speak:
            result = controller.process_command("open the pod bay doors please")
        self.assertTrue(result)
        mock_speak.assert_called_once_with(
            "Something went wrong with 'open the pod bay doors please'.")


if __name__ == "__main__":
    unittest.main()
