"""Regression tests for report.md / error_report.md — Edge-Case domain."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class Edge01UserNameTests(unittest.TestCase):
    """EDGE-01: only a real rename instruction renames the user."""

    def _change(self, command):
        from assistant import commands
        with mock.patch.object(commands, "update_setting") as mock_update, \
             mock.patch.object(commands, "speak"):
            result = commands.change_user_name(command)
        return result, mock_update

    def test_real_instructions_rename(self):
        for command, name in (("my name is sohail", "Sohail"),
                              ("change my name to bob", "Bob"),
                              ("set my name to bob", "Bob"),
                              ("please change my name to bob", "Bob")):
            with self.subTest(command=command):
                result, mock_update = self._change(command)
                self.assertTrue(result)
                mock_update.assert_called_once_with("user_name", name)

    def test_questions_do_not_rename(self):
        for command in ("explain why my name is alex",
                        "do you know what my name is?",
                        "why is my name is on this invoice",
                        "what is my name"):
            with self.subTest(command=command):
                result, mock_update = self._change(command)
                self.assertFalse(result)
                mock_update.assert_not_called()


class Edge02MoveTraversalTests(unittest.TestCase):
    """EDGE-02: a dot-dot destination must not escape the share."""

    def setUp(self):
        import assistant.files as files
        self.files = files
        self.root = Path(tempfile.mkdtemp(prefix="vave-edge02-")).resolve()
        self.shared = self.root / "Documents"
        (self.shared / "subfolder").mkdir(parents=True)
        (self.shared / "test.txt").write_text("data")
        self._real_get_setting = files.get_setting
        files.get_setting = lambda key, default=None: (
            [str(self.shared)] if key == "file_shares"
            else self._real_get_setting(key, default))
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self.files.get_setting = self._real_get_setting
        shutil.rmtree(self.root, ignore_errors=True)

    def test_dotdot_destination_is_refused(self):
        from assistant.files import FileAccessError
        with self.assertRaises(FileAccessError):
            self.files.move("Documents/test.txt", "subfolder/..")
        self.assertTrue((self.shared / "test.txt").exists())

    def test_plain_move_still_works(self):
        entry = self.files.move("Documents/test.txt",
                                "Documents/subfolder/test.txt")
        self.assertTrue((self.shared / "subfolder" / "test.txt").exists())
        self.assertFalse((self.shared / "test.txt").exists())
        self.assertIn("test.txt", entry["name"])


class Edge04UnicodeOutputTests(unittest.TestCase):
    """EDGE-04: console output decoding never crashes."""

    def _run_script(self, body):
        import sys
        import tempfile
        from pathlib import Path
        from assistant import system_tasks
        with tempfile.TemporaryDirectory(prefix="vave-edge04-") as tmp:
            script = Path(tmp) / "probe.py"
            script.write_text(body, encoding="utf-8")
            return system_tasks.run_terminal_command(
                f'"{sys.executable}" "{script}"')

    def test_raw_utf8_bytes_decode(self):
        result = self._run_script(
            "import sys; sys.stdout.buffer.write(b'\\xf0\\x9f\\x9a\\x80')")
        self.assertIn("\U0001F680", result)

    def test_non_utf8_bytes_do_not_crash(self):
        result = self._run_script(
            "import sys; sys.stdout.buffer.write(b'\\x82')")
        self.assertIsInstance(result, str)

    def test_plain_output_unchanged(self):
        result = self._run_script("print('hello')")
        self.assertEqual("hello", result)


class Edge01VolumeRoutingTests(unittest.TestCase):
    """N-EDGE-01: volume words + numbers in questions must not move speakers."""

    def _handle(self, command):
        from assistant import commands
        with mock.patch.object(commands, "set_volume") as mock_set, \
             mock.patch.object(commands, "change_volume_by") as mock_change, \
             mock.patch.object(commands, "mute_volume") as mock_mute:
            handled = commands.handle_volume_command(command)
        return handled, mock_set, mock_change, mock_mute

    def test_real_volume_commands_still_work(self):
        cases = (
            ("volume up", "change", 1),
            ("volume up 20", "change", 1),
            ("volume down", "change", 1),
            ("volume down 20", "change", 1),
            ("volume 40", "set", 1),
            ("set volume to 40", "set", 1),
            ("mute volume", "mute", 1),
            ("mute", "mute", 1),
            ("turn the volume up", "change", 1),
            ("turn the volume down", "change", 1),
            ("increase volume", "change", 1),
            ("decrease volume", "change", 1),
        )
        for command, kind, _ in cases:
            with self.subTest(command=command):
                handled, mock_set, mock_change, mock_mute = self._handle(command)
                self.assertTrue(handled, command)
                if kind == "set":
                    mock_set.assert_called_once()
                elif kind == "change":
                    mock_change.assert_called_once()
                else:
                    mock_mute.assert_called_once()

    def test_volume_up_amount_parsed(self):
        handled, mock_set, mock_change, _mock_mute = self._handle("volume up 20")
        self.assertTrue(handled)
        mock_change.assert_called_once_with(20)

    def test_set_volume_amount_parsed(self):
        handled, mock_set, _mock_change, _mock_mute = self._handle("set volume to 40")
        self.assertTrue(handled)
        mock_set.assert_called_once_with(40)

    def test_questions_fall_through_untouched(self):
        for command in ("what is the volume of a sphere with radius 5?",
                        "calculate volume of 50 boxes",
                        "what is the volume of a sphere of radius 10",
                        "volume"):
            with self.subTest(command=command):
                handled, mock_set, mock_change, mock_mute = self._handle(command)
                self.assertFalse(handled, command)
                mock_set.assert_not_called()
                mock_change.assert_not_called()
                mock_mute.assert_not_called()


class Edge02AssistantNameTests(unittest.TestCase):
    """N-EDGE-02: only a real rename instruction renames the assistant."""

    def _change(self, command):
        from assistant import commands
        with mock.patch.object(commands, "update_setting") as mock_update, \
             mock.patch.object(commands, "speak"):
            result = commands.change_assistant_name(command)
        return result, mock_update

    def test_real_instructions_rename(self):
        for command, name in (("change assistant name to friday", "Friday"),
                              ("set your name to friday", "Friday"),
                              ("please set your name to friday", "Friday"),
                              ("change your name to jarvis", "Jarvis")):
            with self.subTest(command=command):
                result, mock_update = self._change(command)
                self.assertTrue(result)
                mock_update.assert_called_once_with("assistant_name", name)

    def test_conversation_does_not_rename(self):
        for command in ("can i change your name to friday later",
                        "can i change your name to jarvis tomorrow?",
                        "what is your name",
                        "do you like your name"):
            with self.subTest(command=command):
                result, mock_update = self._change(command)
                self.assertFalse(result)
                mock_update.assert_not_called()


class Edge03StdinDisconnectTests(unittest.TestCase):
    """N-EDGE-03: interactive commands get EOF at once, never a 15s hang."""

    def test_stdin_hungry_command_returns_fast(self):
        import sys
        import tempfile
        import time
        from pathlib import Path
        from assistant import system_tasks
        with tempfile.TemporaryDirectory(prefix="vave-edge03-") as tmp:
            script = Path(tmp) / "ask.py"
            script.write_text(
                "import sys; sys.stdout.write('got:%d' % "
                "len(sys.stdin.read()))",
                encoding="utf-8")
            began = time.monotonic()
            result = system_tasks.run_terminal_command(
                f'"{sys.executable}" "{script}"')
            elapsed = time.monotonic() - began
        self.assertEqual("got:0", result)
        self.assertLess(elapsed, 10.0)


if __name__ == "__main__":
    unittest.main()
