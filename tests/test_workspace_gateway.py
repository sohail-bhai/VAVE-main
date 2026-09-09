import unittest
import datetime
from unittest import mock

from assistant.workspace import (
    WorkspaceGateway,
    gateway,
    search_drive,
    read_drive_file,
    upload_drive_file,
    list_drive_files,
    search_emails,
    read_email,
    summarize_emails,
    draft_email,
    send_email,
    get_upcoming_events,
    format_upcoming_events_summary,
    create_calendar_event,
    detect_scheduling_conflicts,
    create_google_doc,
    append_to_google_doc,
    read_google_sheet,
    append_to_google_sheet,
)


class TestGoogleWorkspaceGateway(unittest.TestCase):
    """Verifies all Google Workspace modules and gateway routing."""

    def setUp(self):
        self.patchers = [
            mock.patch("assistant.workspace.auth.load_saved_credentials", return_value=None),
            mock.patch("assistant.workspace.auth.get_google_service", return_value=None),
            mock.patch("assistant.workspace.drive.get_google_service", return_value=None),
            mock.patch("assistant.workspace.gmail.get_google_service", return_value=None),
            mock.patch("assistant.workspace.calendar.get_google_service", return_value=None),
            mock.patch("assistant.workspace.docs_sheets.get_google_service", return_value=None),
            mock.patch("assistant.workspace.slides.get_google_service", return_value=None),
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_gateway_status_and_capabilities(self):
        gw = WorkspaceGateway()
        status = gw.get_status()
        self.assertIn("services", status)
        self.assertIn("drive", status["services"])
        self.assertIn("gmail", status["services"])
        self.assertIn("calendar", status["services"])
        self.assertIn("docs", status["services"])
        self.assertIn("sheets", status["services"])

        caps = gw.list_capabilities()
        self.assertIn("google.drive.search", caps)
        self.assertIn("google.gmail.read", caps)
        self.assertIn("google.calendar.write", caps)
        self.assertIn("google.docs.create", caps)
        self.assertIn("google.sheets.write", caps)

    def test_drive_operations(self):
        # 1. Search
        files = search_drive("Hackwave", limit=5)
        self.assertIsInstance(files, list)
        self.assertTrue(len(files) > 0)
        file_id = files[0]["id"]

        # 2. Read
        content = read_drive_file(file_id)
        self.assertEqual(content["id"], file_id)
        self.assertIn("name", content)

        # 3. Upload
        uploaded = upload_drive_file("Test_Spec.txt", "Automated test file content.")
        self.assertIn("id", uploaded)
        self.assertEqual(uploaded["name"], "Test_Spec.txt")

        # 4. List
        all_files = list_drive_files(limit=5)
        self.assertTrue(len(all_files) > 0)

    def test_gmail_operations(self):
        # 1. Search
        messages = search_emails("Hackwave", max_results=3)
        self.assertIsInstance(messages, list)
        self.assertTrue(len(messages) > 0)
        msg_id = messages[0]["id"]

        # 2. Read
        email_data = read_email(msg_id)
        self.assertEqual(email_data["id"], msg_id)
        self.assertIn("subject", email_data)

        # 3. Summary
        summary = summarize_emails(limit=2)
        self.assertIn("unread email", summary)

        # 4. Draft
        draft = draft_email("judge@hackwave.dev", "Submission Ready", "Here is our project link.")
        self.assertEqual(draft.get("status"), "drafted")
        self.assertEqual(draft.get("to"), "judge@hackwave.dev")

        # 5. Send
        sent = send_email("team@hackwave.dev", "Test Ping", "Ping from unit test.")
        self.assertEqual(sent.get("status"), "sent")

    def test_calendar_operations(self):
        # 1. Upcoming events
        events = get_upcoming_events(max_results=5)
        self.assertIsInstance(events, list)

        # 2. Format summary
        summary = format_upcoming_events_summary(max_results=5)
        self.assertIsInstance(summary, str)

        # 3. Create event
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        created = create_calendar_event("Judges Q&A Session", start_time_iso=now_iso, duration_minutes=30)
        self.assertIn("id", created)
        self.assertEqual(created["summary"], "Judges Q&A Session")

        # 4. Conflict detection
        future_start = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2, minutes=10)).isoformat()
        future_end = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2, minutes=50)).isoformat()
        conflicts = detect_scheduling_conflicts(future_start, future_end)
        self.assertIsInstance(conflicts, list)

    def test_docs_and_sheets_operations(self):
        # 1. Google Doc create
        doc = create_google_doc("Hackwave Architecture Summary", "Initial executive briefing.")
        self.assertIn("documentId", doc)
        doc_id = doc["documentId"]

        # 2. Append to Doc
        appended = append_to_google_doc(doc_id, "Additional section: Zero-Trust Safety Gate.")
        self.assertTrue(appended)

        # 3. Google Sheet read
        sheet_rows = read_google_sheet("mock_sheet_001")
        self.assertIsInstance(sheet_rows, list)
        self.assertTrue(len(sheet_rows) > 0)

        # 4. Google Sheet append
        new_row = [["Deploy Cloud Worker", "In Progress", "DevOps Agent", 4.1]]
        appended_sheet = append_to_google_sheet("mock_sheet_001", new_row)
        self.assertTrue(appended_sheet)

    def test_gateway_execute_capability(self):
        # Test executing drive search via capability string
        res = gateway.execute_capability("google.drive.search", query="Hackwave", limit=2)
        self.assertIsInstance(res, list)
        self.assertTrue(len(res) > 0)

        # Test executing invalid capability
        with self.assertRaises(ValueError):
            gateway.execute_capability("google.unsupported.action")


if __name__ == "__main__":
    unittest.main()
