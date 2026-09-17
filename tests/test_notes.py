"""Direct unit tests for assistant/notes.py.

REL-04: no data/ folder must not crash note capture or reading.
EDGE-03: read_notes opens the file once and survives it vanishing.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


class NotesFileTests(unittest.TestCase):
    def setUp(self):
        import assistant.notes as notes
        self.notes = notes
        self.tmp = TemporaryDirectory(prefix="vave-notes-test-")
        self.addCleanup(self.tmp.cleanup)
        self.missing_dir = Path(self.tmp.name) / "fresh" / "clone"
        self.patch_file = mock.patch.object(
            notes, "NOTES_FILE", self.missing_dir / "notes.txt")
        self.patch_file.start()
        self.addCleanup(self.patch_file.stop)
        self.spoken = []
        self.patch_speak = mock.patch.object(
            notes, "speak", side_effect=self.spoken.append)
        self.patch_speak.start()
        self.addCleanup(self.patch_speak.stop)

    def test_add_note_creates_missing_data_dir(self):
        with mock.patch.object(self.notes, "listen", return_value="buy milk"):
            self.notes.add_note()
        content = (self.missing_dir / "notes.txt").read_text(encoding="utf-8")
        self.assertIn("buy milk", content)
        self.assertIn("Note saved.", self.spoken)

    def test_read_notes_missing_file_says_empty(self):
        self.notes.read_notes()
        self.assertEqual(["You have no notes."], self.spoken)

    def test_read_notes_reads_back_what_was_added(self):
        with mock.patch.object(self.notes, "listen", return_value="buy milk"):
            self.notes.add_note()
        self.spoken.clear()
        self.notes.read_notes()
        self.assertEqual("Reading your notes.", self.spoken[0])
        self.assertTrue(any("buy milk" in line for line in self.spoken[1:]))

    def test_read_notes_survives_file_vanishing_mid_read(self):
        with mock.patch("builtins.open",
                        side_effect=FileNotFoundError("gone")):
            self.notes.read_notes()
        self.assertEqual(["You have no notes."], self.spoken)

    def test_clear_notes_creates_missing_data_dir(self):
        self.notes.clear_notes()
        self.assertTrue((self.missing_dir / "notes.txt").exists())
        self.assertIn("All notes cleared.", self.spoken)


if __name__ == "__main__":
    unittest.main()
