"""Unit tests for Phase 5 Workspace Refinements (Wave 1: R1-R4)."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from assistant.control.store import ControlStore
from assistant.workspace.drive_indexer import DriveSemanticIndexer, _get_drive_collection
from assistant.workspace.gmail import validate_email_recipient
from tests.support import VaveTestCase


class WorkspaceRefinementsTests(VaveTestCase):
    def setUp(self):
        super().setUp()
        self.db_path = Path(self.temp_dir) / "control.db"

        # Mock out Google service to keep tests offline & deterministic
        patcher = mock.patch("assistant.workspace.drive.get_google_service", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        super().tearDown()

    def test_r2_email_regex_disallows_consecutive_and_edge_dots(self):
        # Valid cases
        self.assertTrue(validate_email_recipient("user@example.com"))
        self.assertTrue(validate_email_recipient("first.last@domain.co"))
        self.assertTrue(validate_email_recipient("user+tag@domain.org"))
        self.assertTrue(validate_email_recipient("user_name@domain.net"))
        self.assertTrue(validate_email_recipient("u-name@domain.io"))

        # RFC 5322 invalid dot placements in local part
        self.assertFalse(validate_email_recipient("user..name@example.com"))
        self.assertFalse(validate_email_recipient(".user@example.com"))
        self.assertFalse(validate_email_recipient("user.@example.com"))
        self.assertFalse(validate_email_recipient("user...name@example.com"))

    def test_r1_indexer_close_and_context_manager(self):
        # 1. Standalone owned store
        indexer = DriveSemanticIndexer()
        # Trigger lazy init
        store = indexer.store
        self.assertIsNotNone(store)
        self.assertIsNotNone(store._connection)
        indexer.close()
        # Ensure store connection closed
        self.assertIsNone(store._connection)

        # 2. Context manager pattern
        test_store = ControlStore(self.db_path)
        with DriveSemanticIndexer(store=test_store) as idx:
            self.assertEqual(idx.store, test_store)
        # Store closed on exit
        self.assertIsNone(test_store._connection)

    def test_r3_transactional_rollback_on_store_failure(self):
        store = ControlStore(self.db_path)
        indexer = DriveSemanticIndexer(store=store)
        collection = _get_drive_collection()

        # Mock save_drive_sync_file to simulate SQLite failure
        with mock.patch.object(store, "save_drive_sync_file", side_effect=sqlite3.OperationalError("disk I/O failure")):
            with self.assertRaises(sqlite3.OperationalError):
                indexer.sync_drive_index()

        # If ChromaDB collection is active, ensure newly added chunks are not left orphaned
        if collection is not None:
            mock_res = collection.get(where={"file_id": {"$eq": "mock_doc_001"}})
            self.assertEqual(0, len(mock_res.get("ids", [])))

        store.close()

    def test_r4_unreadable_file_handling(self):
        store = ControlStore(self.db_path)
        indexer = DriveSemanticIndexer(store=store)

        fake_files = [
            {
                "id": "unreadable_001",
                "name": "Corrupt.pdf",
                "mimeType": "application/pdf",
                "modifiedTime": "2026-09-10T00:00:00Z",
                "content": "",
            }
        ]
        with mock.patch("assistant.workspace.drive_indexer.list_drive_files", return_value=fake_files):
            with mock.patch("assistant.workspace.drive_indexer.read_drive_file", return_value={"id": "unreadable_001", "content": "", "unreadable": True}):
                res = indexer.sync_drive_index()

                self.assertEqual("success", res["status"])
                record = store.get_drive_sync_file("unreadable_001")
                self.assertIsNotNone(record)
                self.assertEqual(0, record["chunk_count"])

        store.close()


if __name__ == "__main__":
    unittest.main()
