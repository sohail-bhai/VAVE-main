"""Tests for Verified Gmail Drafting and Sending with Scoped Credentials.

Tests input validation, verified draft creation, zero-trust send approvals,
and capability-scoped credential protection.
"""

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from assistant.api.app import create_app
from assistant.api.auth import ApiSecurity
from assistant.control.service import ControlPlane
from assistant.control.store import ControlStore
from assistant.control.secrets import SecretStore, load_key
from assistant.workspace import auth as workspace_auth
from assistant.workspace.gmail import (
    validate_email_recipient,
    draft_email,
    send_email,
)


class GmailVerificationUnitTests(unittest.TestCase):
    def setUp(self):
        self.patchers = [
            mock.patch.object(workspace_auth, "load_saved_credentials", return_value=None),
            mock.patch("assistant.workspace.gmail.get_google_service", return_value=None),
            mock.patch.object(workspace_auth, "get_google_service", return_value=None),
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_validate_email_recipient_enforces_syntax(self):
        self.assertTrue(validate_email_recipient("user@example.com"))
        self.assertTrue(validate_email_recipient("first.last+tag@sub.domain.org"))
        self.assertFalse(validate_email_recipient("notanemail"))
        self.assertFalse(validate_email_recipient("@domain.com"))
        self.assertFalse(validate_email_recipient("user@"))
        self.assertFalse(validate_email_recipient(""))
        self.assertFalse(validate_email_recipient(None))

    def test_draft_email_rejects_invalid_recipient(self):
        with self.assertRaises(ValueError):
            draft_email(to="bad-email", subject="Test", body="Hello")

    def test_draft_email_verified_returns_confirmation(self):
        res = draft_email(to="alex@example.com", subject="Project Update", body="Here are notes.")
        self.assertEqual("drafted", res["status"])
        self.assertTrue(res["verified"])
        self.assertEqual("alex@example.com", res["to"])
        self.assertTrue(res["draft_id"].startswith("draft_") or res["id"])

    def test_send_email_rejects_invalid_recipient(self):
        with self.assertRaises(ValueError):
            send_email(to="notanemail", subject="Test", body="Hello")

    def test_send_email_verified_returns_confirmation(self):
        res = send_email(to="alex@example.com", subject="Approved Deliverable", body="All tests green.")
        self.assertEqual("sent", res["status"])
        self.assertTrue(res["verified"])
        self.assertEqual("alex@example.com", res["to"])
        self.assertTrue(res["message_id"] or res["id"])


class GmailApiVerificationTests(unittest.TestCase):
    def setUp(self):
        self.plane = ControlPlane(store=ControlStore(":memory:"))
        security = ApiSecurity(self.plane.store, trust_local=True,
                               trusted_hosts=("testclient", "127.0.0.1"))
        self.client = TestClient(create_app(control=self.plane, security=security))
        self.patchers = [
            mock.patch.object(workspace_auth, "load_saved_credentials", return_value=None),
            mock.patch("assistant.workspace.gmail.get_google_service", return_value=None),
            mock.patch.object(workspace_auth, "get_google_service", return_value=None),
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        if hasattr(self, "plane") and self.plane:
            self.plane.close()

    def approve_all(self):
        for approval in self.plane.list_approvals(pending_only=True):
            self.plane.resolve_approval(approval.id, True)

    def test_draft_endpoint_validates_email_and_succeeds(self):
        # Invalid email returns 400
        bad = self.client.post("/api/google/gmail/draft",
                               json={"to": "invalid", "subject": "Hi", "body": "text"})
        self.assertEqual(400, bad.status_code)

        # Valid email drafts without requiring approval
        good = self.client.post("/api/google/gmail/draft",
                                json={"to": "partner@corp.com", "subject": "Hi", "body": "text"})
        self.assertEqual(201, good.status_code)
        body = good.json()
        self.assertTrue(body["draft"]["verified"])
        self.assertEqual("partner@corp.com", body["draft"]["to"])

    def test_send_endpoint_waits_for_approval_and_verifies_on_release(self):
        payload = {"to": "investor@venture.com", "subject": "Term Sheet", "body": "Attached."}

        # First attempt: held for approval
        resp = self.client.post("/api/google/gmail/send", json=payload)
        self.assertEqual(202, resp.status_code)
        self.assertEqual("waiting_approval", resp.json()["status"])

        # Approve
        self.approve_all()

        # Second attempt: permitted and verified
        allowed = self.client.post("/api/google/gmail/send", json=payload)
        self.assertEqual(200, allowed.status_code)
        body = allowed.json()
        self.assertEqual("sent", body["status"])
        self.assertTrue(body["message"]["verified"])
        self.assertEqual("investor@venture.com", body["message"]["to"])


class ScopedGmailCredentialTests(unittest.TestCase):
    def setUp(self):
        self.store = ControlStore(":memory:")
        self.secrets = SecretStore(self.store, key=load_key())

    def tearDown(self):
        self.store.close()

    def test_gmail_scoped_secret_only_resolves_for_matching_capability(self):
        self.secrets.put("gmail_token", "ya29.secret_token_val",
                         allowed_capabilities="google.gmail.*,email.*")

        # Resolving under google.gmail.send succeeds
        val = self.secrets.resolve("secret://gmail_token", capability="google.gmail.send")
        self.assertEqual("ya29.secret_token_val", val)

        # Resolving under google.drive.read is refused
        with self.assertRaises(PermissionError):
            self.secrets.resolve("secret://gmail_token", capability="google.drive.read")


if __name__ == "__main__":
    unittest.main()
