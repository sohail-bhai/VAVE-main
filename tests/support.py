"""Shared test support and isolation base classes for VAVE.

Provides VaveTestCase to ensure tests never write to real user data or leave
leaky global state across runs.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from assistant import call_context
from assistant import guard
from assistant import audit
from assistant import memory
from assistant.workspace import drive_indexer
from assistant.control import service


class VaveTestCase(unittest.TestCase):
    """Base test case that redirects all data persistence to an isolated temp directory."""

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.mkdtemp(prefix="vave_test_data_")
        self._orig_vave_data_dir = os.environ.get("VAVE_DATA_DIR")
        os.environ["VAVE_DATA_DIR"] = self.temp_dir

        # Snapshot module-level state
        self._orig_kill_switch = guard._kill_switch
        self._orig_guard_bus = guard._bus
        self._orig_control_plane = service._control_plane
        self._orig_audit_path = audit._audit_path

        # Clear context taint and origin
        call_context.clear_taint()
        self._origin_token = call_context.set_origin("voice")

        # Point ChromaDB and drive collection at isolated temp directory
        memory.reset_chroma(self.temp_dir)
        drive_indexer.reset_drive_collection()

    def tearDown(self):
        # Reset and close any active singletons
        if service._control_plane is not None:
            try:
                service._control_plane.close()
            except Exception:
                pass
            service._control_plane = None

        # Reset drive collection and chroma back to clean state
        drive_indexer.reset_drive_collection()

        # Restore module state
        guard._kill_switch = self._orig_kill_switch
        guard._bus = self._orig_guard_bus
        audit._audit_path = self._orig_audit_path
        call_context.clear_taint()
        if hasattr(self, "_origin_token"):
            call_context.reset_origin(self._origin_token)

        # Restore environment
        if self._orig_vave_data_dir is not None:
            os.environ["VAVE_DATA_DIR"] = self._orig_vave_data_dir
        else:
            os.environ.pop("VAVE_DATA_DIR", None)

        memory.reset_chroma()

        # Clean up temporary files
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

        super().tearDown()
