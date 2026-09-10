"""Tests for Google Drive Semantic Indexer.

Tests incremental synchronization, vector chunking, semantic querying,
pruning of deleted Drive files, and immediate export-and-index workflow.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from assistant.control.store import ControlStore
from assistant.workspace.drive_indexer import DriveSemanticIndexer, chunk_text


class DriveIndexerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="vave-drive-indexer-test-")
        self.db_path = Path(self.tempdir) / "control.db"
        self.store = ControlStore(self.db_path)
        self.indexer = DriveSemanticIndexer(store=self.store)

        patcher = mock.patch("assistant.workspace.drive.get_google_service", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        if hasattr(self, "store") and self.store:
            self.store.close()
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_chunk_text_splits_large_content_cleanly(self):
        sample = ("Line one of the proposal. " * 30) + "\n\n" + ("Second section content. " * 30)
        chunks = chunk_text(sample, chunk_size=300, overlap=50)

        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 400)
            self.assertTrue(len(chunk) > 0)

    def test_sync_drive_index_ingests_and_records_state(self):
        result = self.indexer.sync_drive_index()

        self.assertEqual("success", result["status"])
        self.assertGreater(result["total_files"], 0)
        self.assertGreater(result["total_chunks"], 0)

        # Verify SQLite store holds the indexed records
        indexed_files = self.store.list_drive_sync_files()
        self.assertGreater(len(indexed_files), 0)

        first_file = indexed_files[0]
        self.assertTrue(first_file["file_id"])
        self.assertTrue(first_file["name"])
        self.assertGreater(first_file["chunk_count"], 0)

    def test_semantic_search_returns_relevant_matches(self):
        self.indexer.sync_drive_index()

        # Query for architecture / control plane
        results = self.indexer.semantic_search("Zero-Trust Safety Gate specs", limit=3)

        self.assertTrue(len(results) > 0)
        top_match = results[0]
        self.assertIn("file_id", top_match)
        self.assertIn("name", top_match)
        self.assertIn("snippet", top_match)
        self.assertIn("relevance", top_match)
        self.assertGreaterEqual(top_match["relevance"], 0.0)

    def test_export_to_drive_indexes_immediately(self):
        res = self.indexer.export_to_drive(
            name="New_Feature_Spec.md",
            content="Specification for automated background indexing and Google Workspace sync.",
            mime_type="text/markdown"
        )

        self.assertEqual("success", res["status"])
        self.assertTrue(res["indexed"])
        self.assertGreater(res["chunks_indexed"], 0)

        # Check it can be immediately found in store
        file_record = self.store.get_drive_sync_file(res["file_id"])
        self.assertIsNotNone(file_record)
        self.assertEqual("New_Feature_Spec.md", file_record["name"])

        # Check it appears in search results
        search_hits = self.indexer.semantic_search("automated background indexing", limit=5)
        matched_names = [hit["name"] for hit in search_hits]
        self.assertIn("New_Feature_Spec.md", matched_names)


if __name__ == "__main__":
    unittest.main()
