"""Tests verifying that test suites do not leak writes into real production data."""

import os
from pathlib import Path

from assistant import memory
from assistant.control.store import get_default_db_path, DEFAULT_DB_PATH
from assistant.workspace.drive_indexer import DriveSemanticIndexer, _get_drive_collection
from tests.support import VaveTestCase


class DataDirGuardTests(VaveTestCase):
    """Guard tests verifying the test isolation boundary."""

    def test_vave_data_dir_redirects_memory_and_chroma(self):
        self.assertTrue(os.environ.get("VAVE_DATA_DIR"))
        self.assertEqual(str(memory.DATA_DIR), self.temp_dir)

        # Vector memory add writes to temp directory
        success = memory.remember_fact("test isolation fact")
        self.assertTrue(success)

        # Verify chroma_db was created in temp_dir
        temp_chroma = Path(self.temp_dir) / "chroma_db"
        self.assertTrue(temp_chroma.exists())

    def test_default_db_path_redirects_to_temp_dir(self):
        default_path = get_default_db_path()
        self.assertEqual(default_path, Path(self.temp_dir) / "control.db")
        self.assertNotEqual(default_path, DEFAULT_DB_PATH)

    def test_drive_indexer_uses_isolated_temp_chroma(self):
        indexer = DriveSemanticIndexer()
        # Ensure store is inside temp dir
        self.assertEqual(indexer.store.db_path, Path(self.temp_dir) / "control.db")

        collection = _get_drive_collection()
        self.assertIsNotNone(collection)

        indexer.close()
