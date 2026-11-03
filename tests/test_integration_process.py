"""Security regressions for external CLI launcher unwrapping."""

import os
import tempfile
import unittest
from pathlib import Path

from assistant.integrations._process import safe_cli_command


@unittest.skipUnless(os.name == "nt", "Windows command shims only")
class SafeCliCommandTests(unittest.TestCase):
    def test_batch_wrapper_is_unwrapped_without_cmd(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable = root / "language_server.exe"
            executable.write_bytes(b"")
            launcher = root / "agentapi.bat"
            launcher.write_text(
                f'@echo off\n"{executable}" agentapi %*\n',
                encoding="utf-8",
            )

            command = safe_cli_command(str(launcher), "new-conversation", "A & B")

            self.assertEqual(str(executable), command[0])
            self.assertEqual("agentapi", command[1])
            self.assertEqual("A & B", command[-1])
            self.assertNotEqual("cmd.exe", Path(command[0]).name.lower())

    def test_npm_wrapper_is_unwrapped_to_node(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            node = root / "node.exe"
            node.write_bytes(b"")
            script = root / "node_modules" / "tool" / "cli.js"
            script.parent.mkdir(parents=True)
            script.write_text("", encoding="utf-8")
            launcher = root / "tool.cmd"
            launcher.write_text(
                '@echo off\n"%dp0%\\node.exe" '
                '"%dp0%\\node_modules\\tool\\cli.js" %*\n',
                encoding="utf-8",
            )

            command = safe_cli_command(str(launcher), "status", "A & B")

            self.assertEqual(str(node), command[0])
            self.assertEqual(str(script), command[1])
            self.assertEqual(["status", "A & B"], command[-2:])


if __name__ == "__main__":
    unittest.main()
