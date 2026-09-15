"""Unit tests for the Activity page Journal view."""

import importlib.util
import unittest

from tests.support import VaveTestCase

HAS_TKINTER = importlib.util.find_spec("tkinter") is not None

if HAS_TKINTER:
    from gui.app import VaveDashboardApp
    from gui.pages.activity_page import journal_items, _journal_item


@unittest.skipUnless(HAS_TKINTER, "tkinter is not installed on this machine")
class ActivityJournalTests(VaveTestCase):
    def test_journal_item_shapes_timeline_row(self):
        item = _journal_item({
            "ts": 0, "request": "what time is it", "origin": "voice",
            "tools_run": ["tell_time"], "step_count": 1,
            "outcome": "completed", "reason": "",
        })
        self.assertEqual("Journal", item["category"])
        self.assertIn("what time is it", item["title"])
        self.assertIn("completed", item["detail"])
        self.assertIn("tell_time", item["detail"])

    def test_activity_page_renders_journal_filter(self):
        from pathlib import Path

        from assistant import audit, task_journal

        # Bootstrap (inside the app) points the ledger at VAVE_DATA_DIR,
        # which VaveTestCase already aimed at this test's temp dir. Match
        # that filename so the probe lands where the page reads.
        audit.configure(Path(self.temp_dir) / "audit.jsonl",
                        max_bytes=1_000_000, backup_count=3)

        app = VaveDashboardApp()
        try:
            task_journal.record_task_attempt(request="gui journal probe",
                                             outcome="completed", tools_run=["tell_time"])
            app.navigate_to("activity")
            page = app.pages["activity"]
            page._set_filter("Journal")
            app.update_idletasks()
            cards = page.timeline_container.winfo_children()
            self.assertTrue(cards)
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()
