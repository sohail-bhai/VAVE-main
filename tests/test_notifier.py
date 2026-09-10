"""Tests for notification persistence and delivery."""

import shutil
import tempfile
import unittest
from pathlib import Path

from assistant.control.models import ActivityEvent, EventType
from assistant.control.notifier import Notification, Notifier
from assistant.control.service import ControlPlane
from assistant.control.store import ControlStore


class NotifierPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="vave-notifier-test-")
        self.db_path = Path(self.tempdir) / "control.db"
        self.store = ControlStore(self.db_path)
        self.plane = ControlPlane(store=self.store)
        self.notifier = Notifier(self.plane)

    def tearDown(self):
        if self.notifier:
            self.notifier.close()
        if self.plane:
            self.plane.close()
        self.store.close()
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_notification_carries_read_flag(self):
        event = ActivityEvent(type=EventType.APPROVAL_REQUESTED, message="Approval needed")
        notification = Notification(event, "action")
        self.assertFalse(notification.read)
        data = notification.to_dict()
        self.assertIn("read", data)
        self.assertFalse(data["read"])

    def test_notifications_persist_to_sqlite(self):
        self.plane.record("Something went wrong", EventType.TASK_FAILED, task_id="t1")

        recent = self.notifier.recent()
        self.assertEqual(1, len(recent))
        self.assertEqual("Something went wrong", recent[0]["message"])
        self.assertEqual("problem", recent[0]["urgency"])
        self.assertFalse(recent[0]["read"])

        new_plane = ControlPlane(store=ControlStore(self.db_path))
        new_notifier = Notifier(new_plane)
        try:
            persisted = new_notifier.recent()
            self.assertEqual(1, len(persisted))
            self.assertEqual("Something went wrong", persisted[0]["message"])
        finally:
            new_notifier.close()
            new_plane.close()

    def test_marking_notification_read(self):
        event = self.plane.record("Please approve this", EventType.APPROVAL_REQUESTED, task_id="t2")
        recent = self.notifier.recent()
        self.assertEqual(1, len(recent))
        notif_id = recent[0]["id"]

        marked = self.notifier.mark_read(notif_id)
        self.assertIsNotNone(marked)
        self.assertEqual(1, marked["read"])

        unreads = self.notifier.recent(unread_only=True)
        self.assertEqual([], unreads)

        all_items = self.notifier.recent(unread_only=False)
        self.assertEqual(1, len(all_items))
        self.assertEqual(1, all_items[0]["read"])


if __name__ == "__main__":
    unittest.main()
