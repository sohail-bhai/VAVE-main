"""Public downloads must not leak local credentials or runtime state."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import build_release


class ReleasePackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.write("pyproject.toml", '[project]\nversion = "1.3.0"\n')
        self.write("main.py", "print('public source')\n")
        self.write("requirements.txt", "customtkinter\n")

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        return path

    def test_package_excludes_private_files_and_preserves_source(self):
        private = {
            "config.json": '{"user_name":"Private user"}',
            "config.local.json": "private-override",
            ".env": "private-env",
            "data/notes.txt": "private-notes",
            "data/secret.key": "private-key",
            "logs/audit.jsonl": "private-log",
            "assets/screenshots/private.png": "private-screen",
            "assistant/.env.local": "nested-private-env",
            "assistant/credentials.json": "nested-private-credential",
            "assistant/token_workspace.json": "nested-private-token",
            "assistant/runtime.sqlite3": "nested-private-db",
            "assistant/secret.key": "nested-private-key",
            "assistant/__pycache__/module.pyc": "private-cache",
        }
        for name, value in private.items():
            self.write(name, value)
        self.write("assistant/ai_brain.py", "# public agent\n")
        self.write("assets/icons/public.svg", "<svg/>\n")
        self.write("mobile/pwa/index.html", "<title>Phone client</title>\n")
        archive = build_release.build_release(self.root)
        with zipfile.ZipFile(archive) as bundle:
            names = set(bundle.namelist())
            self.assertIn("VAVE/assistant/ai_brain.py", names)
            self.assertIn("VAVE/assets/icons/public.svg", names)
            self.assertIn("VAVE/mobile/pwa/index.html", names)
            for name in private:
                if name not in {"config.json", "data/notes.txt"}:
                    self.assertNotIn("VAVE/" + name, names)
            config = json.loads(bundle.read("VAVE/config.json"))
            self.assertEqual(config["user_name"], "User")
            self.assertEqual(config["ollama_models_dir"], "~/.ollama/models")
            self.assertFalse(config["wake_word_enabled"])
            self.assertFalse(config["speech_enabled"])
            self.assertEqual(bundle.read("VAVE/data/notes.txt"), b"")
            for member in bundle.namelist():
                self.assertNotIn(b"private-", bundle.read(member))
        for name, value in private.items():
            self.assertEqual((self.root / name).read_text(encoding="utf-8"), value)
        self.assertFalse((self.root / "Start_VAVE.bat").exists())

    def test_manifest_matches_archive_and_scripts_surface_failures(self):
        archive = build_release.build_release(self.root, self.root / "showcase/downloads")
        manifest = json.loads((archive.parent / "release.json").read_text())
        self.assertEqual(manifest["size_bytes"], archive.stat().st_size)
        self.assertEqual(manifest["sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertEqual(manifest["file"], archive.name)
        with zipfile.ZipFile(archive) as bundle:
            setup = bundle.read("VAVE/Setup_VAVE.bat").decode()
            self.assertIn("if errorlevel 1 goto failed", setup)
            self.assertIn('cd /d "%~dp0"', setup)
            self.assertNotIn("2>&1", setup.split("-m pip install")[1])
            self.assertIn("Start_VAVE.bat", bundle.read("VAVE/QUICKSTART.txt").decode())

    def test_failed_build_does_not_replace_previous_archive(self):
        archive = build_release.build_release(self.root)
        previous = archive.read_bytes()
        with patch.object(zipfile.ZipFile, "write", side_effect=OSError("example failure")):
            with self.assertRaises(OSError):
                build_release.build_release(self.root)
        self.assertEqual(archive.read_bytes(), previous)
        self.assertEqual(list(archive.parent.glob(".vave-release-*")), [])

    def test_public_file_check_rejects_symlink(self):
        path = self.write("assistant/example.py", "# public\n")
        with patch.object(Path, "is_symlink", return_value=True):
            self.assertFalse(build_release.public_source_file(path, self.root.resolve()))

    def test_version_cannot_escape_output_directory(self):
        self.write("pyproject.toml", '[project]\nversion = "../private"\n')
        with self.assertRaises(ValueError):
            build_release.build_release(self.root)


if __name__ == "__main__":
    unittest.main()
