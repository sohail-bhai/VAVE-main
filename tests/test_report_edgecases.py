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


if __name__ == "__main__":
    unittest.main()
