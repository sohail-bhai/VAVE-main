"""Tests for what VAVE remembers about a website.

The second visit should not start from nothing. These check notes are kept per
domain, handed back as a hint, and capped so they stay small enough to sit in
a prompt.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from assistant.site_memory import MAX_NOTES_PER_SITE, SiteMemory, domain_of


class DomainTests(unittest.TestCase):
    def test_the_domain_is_taken_from_any_address(self):
        self.assertEqual("gitlab.com", domain_of("https://gitlab.com/x/y/-/issues"))
        self.assertEqual("gitlab.com", domain_of("gitlab.com"))
        self.assertEqual("gitlab.com", domain_of("https://www.gitlab.com/a"))

    def test_nothing_in_gives_nothing_out(self):
        self.assertEqual("", domain_of(""))
        self.assertEqual("", domain_of(None))


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="vave-sites-")
        self.memory = SiteMemory(Path(self.tempdir) / "site_notes.json")

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_a_note_comes_back_for_any_page_on_that_site(self):
        self.memory.remember("https://gitlab.com/group/repo/-/issues",
                             "Issues live under /-/issues")

        self.assertEqual(["Issues live under /-/issues"],
                         self.memory.recall("https://gitlab.com/somewhere/else"))

    def test_a_failed_write_leaves_the_old_file_intact(self):
        self.memory.remember("https://example.com", "first note")
        before = self.memory.path.read_text(encoding="utf-8")
        with mock.patch("os.replace", side_effect=OSError("disk vanished")):
            self.memory.remember("https://example.com", "second note")
        self.assertEqual(before,
                         self.memory.path.read_text(encoding="utf-8"))
        leftovers = list(Path(self.tempdir).glob(".site_notes.*.tmp"))
        self.assertEqual([], leftovers)
        self.assertEqual(["first note"],
                         self.memory.recall("https://example.com"))

    def test_another_site_is_not_told_what_this_one_learned(self):
        self.memory.remember("https://gitlab.com", "needs a login")

        self.assertEqual([], self.memory.recall("https://github.com"))

    def test_the_same_note_twice_is_kept_once_and_stays_recent(self):
        self.memory.remember("https://x.com", "search box is element 3")
        self.memory.remember("https://x.com", "results load slowly")
        self.memory.remember("https://x.com", "search box is element 3")

        self.assertEqual(["results load slowly", "search box is element 3"],
                         self.memory.recall("https://x.com"))

    def test_notes_do_not_grow_without_limit(self):
        for index in range(MAX_NOTES_PER_SITE + 5):
            self.memory.remember("https://x.com", f"note {index}")

        self.assertEqual(MAX_NOTES_PER_SITE, len(self.memory.recall("https://x.com")))

    def test_a_very_long_note_is_shortened(self):
        self.memory.remember("https://x.com", "y" * 500)

        self.assertLessEqual(len(self.memory.recall("https://x.com")[0]), 200)

    def test_notes_survive_a_restart(self):
        self.memory.remember("https://x.com", "the login is at /session/new")

        reopened = SiteMemory(self.memory.path)

        self.assertEqual(["the login is at /session/new"],
                         reopened.recall("https://x.com"))

    def test_the_hint_is_written_for_the_model(self):
        self.memory.remember("https://gitlab.com", "issues live under /-/issues")

        hint = self.memory.as_hint("https://gitlab.com/group/repo")

        self.assertIn("What you learned about gitlab.com before", hint)
        self.assertIn("- issues live under /-/issues", hint)

    def test_a_site_with_no_notes_produces_no_hint(self):
        self.assertEqual("", self.memory.as_hint("https://unknown.example"))

    def test_a_site_can_be_forgotten(self):
        self.memory.remember("https://x.com", "something wrong")

        self.memory.forget("https://x.com")

        self.assertEqual([], self.memory.recall("https://x.com"))

    def test_known_sites_can_be_listed(self):
        self.memory.remember("https://a.com", "one")
        self.memory.remember("https://b.com", "two")

        self.assertEqual(["a.com", "b.com"], self.memory.sites())

    def test_an_unreadable_file_is_treated_as_empty_rather_than_crashing(self):
        self.memory.path.parent.mkdir(parents=True, exist_ok=True)
        self.memory.path.write_text("this is not json", encoding="utf-8")

        self.assertEqual([], self.memory.recall("https://x.com"))
        self.assertEqual(["fresh start"], self.memory.remember("https://x.com",
                                                               "fresh start"))


if __name__ == "__main__":
    unittest.main()
