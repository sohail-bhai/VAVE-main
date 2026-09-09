"""Tests for reaching Google through the control plane.

Two rules matter here and are tested rather than assumed:

* A screen must never be able to show example data as the user's own, so every
  answer says whether it came from Google.
* Anything the outside world would notice - mail leaving, an event appearing on
  a calendar - waits for the user, and the approval covers those exact
  arguments rather than the action in general.
"""

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from assistant.api.app import create_app
from assistant.api.auth import ApiSecurity
from assistant.control.service import ControlPlane
from assistant.control.store import ControlStore
from assistant.workspace import auth as workspace_auth
from assistant.workspace.gateway import WorkspaceGateway


class GoogleApiTestCase(unittest.TestCase):
    def setUp(self):
        self.plane = ControlPlane(store=ControlStore(":memory:"))
        security = ApiSecurity(self.plane.store, trust_local=True,
                               trusted_hosts=("testclient", "127.0.0.1"))
        self.client = TestClient(create_app(control=self.plane, security=security))
        self.patchers = [
            mock.patch.object(workspace_auth, "load_saved_credentials", return_value=None),
            mock.patch("assistant.workspace.docs_sheets.get_google_service", return_value=None),
            mock.patch("assistant.workspace.slides.get_google_service", return_value=None),
            mock.patch.object(workspace_auth, "get_google_service", return_value=None),
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    def approve_everything(self):
        for approval in self.plane.list_approvals(pending_only=True):
            self.plane.resolve_approval(approval.id, True)


class HonestyTests(GoogleApiTestCase):
    """Google is not connected in a test, so every answer must admit it."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(
            workspace_auth, "connection_state",
            return_value={"state": workspace_auth.NOT_CONFIGURED,
                          "connected": False,
                          "mode": "demo",
                          "detail": "credentials.json is missing."}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_reading_says_it_is_showing_examples(self):
        body = self.client.get("/api/google/drive").json()

        self.assertFalse(body["live"])
        self.assertIn("isn't connected", body["notice"])
        # There is still something to render, which is the point of demo data.
        self.assertTrue(body["items"])

    def test_gmail_and_calendar_say_the_same(self):
        for path in ("/api/google/gmail", "/api/google/calendar"):
            with self.subTest(path=path):
                body = self.client.get(path).json()
                self.assertFalse(body["live"])
                self.assertIn("notice", body)

    def test_status_names_the_state_rather_than_only_a_flag(self):
        body = self.client.get("/api/google/status").json()

        self.assertFalse(body["connected"])
        self.assertEqual("demo", body["mode"])
        self.assertIn(body["state"], (workspace_auth.NOT_CONFIGURED,
                                      workspace_auth.NEEDS_AUTHORIZATION))
        # A reason a person can act on, not just "false".
        self.assertTrue(body["detail"])

    def test_connecting_is_refused_when_there_is_no_oauth_client(self):
        with mock.patch.object(workspace_auth, "connection_state",
                               return_value={"state": workspace_auth.NOT_CONFIGURED,
                                             "detail": "credentials.json is missing."}):
            response = self.client.post("/api/google/connect")

        self.assertEqual(409, response.status_code)


class ApprovalTests(GoogleApiTestCase):
    def test_sending_mail_waits_for_the_user(self):
        response = self.client.post("/api/google/gmail/send",
                                    json={"to": "a@b.com", "subject": "Hi", "body": "x"})

        self.assertEqual(202, response.status_code)
        self.assertEqual("waiting_approval", response.json()["status"])
        self.assertEqual(1, len(self.plane.list_approvals(pending_only=True)))

    def test_an_approval_releases_exactly_one_send(self):
        payload = {"to": "a@b.com", "subject": "Hi", "body": "x"}
        self.client.post("/api/google/gmail/send", json=payload)
        self.approve_everything()

        allowed = self.client.post("/api/google/gmail/send", json=payload)
        self.assertEqual(200, allowed.status_code)
        self.assertEqual("sent", allowed.json()["status"])

        # The grant is spent, so the identical call has to ask again.
        again = self.client.post("/api/google/gmail/send", json=payload)
        self.assertEqual(202, again.status_code)

    def test_an_approval_does_not_cover_a_different_recipient(self):
        """Approving "email Sam" must not authorise emailing someone else."""
        self.client.post("/api/google/gmail/send",
                         json={"to": "sam@b.com", "subject": "Hi", "body": "x"})
        self.approve_everything()

        other = self.client.post("/api/google/gmail/send",
                                 json={"to": "someone@else.com", "subject": "Hi",
                                       "body": "x"})

        self.assertEqual(202, other.status_code)
        self.assertEqual("waiting_approval", other.json()["status"])

    def test_creating_a_calendar_event_waits_too(self):
        response = self.client.post("/api/google/calendar/events",
                                    json={"summary": "Standup"})

        self.assertEqual(202, response.status_code)
        self.assertEqual("waiting_approval", response.json()["status"])

    def test_a_draft_needs_no_approval_because_it_sends_nothing(self):
        response = self.client.post("/api/google/gmail/draft",
                                    json={"to": "a@b.com", "subject": "Hi", "body": "x"})

        self.assertEqual(201, response.status_code)
        self.assertEqual([], self.plane.list_approvals(pending_only=True))


class MakingThingsTests(GoogleApiTestCase):
    def test_a_document_can_be_created(self):
        response = self.client.post("/api/google/docs",
                                    json={"title": "Notes", "content": "hello"})

        self.assertEqual(201, response.status_code)
        self.assertEqual("Notes", response.json()["document"]["title"])

    def test_a_deck_gets_a_title_slide_plus_one_per_entry(self):
        response = self.client.post("/api/google/slides",
                                    json={"title": "Demo", "slides": ["Intro", "Results"]})

        self.assertEqual(201, response.status_code)
        self.assertEqual(3, response.json()["presentation"]["slides"])

    def test_a_deck_accepts_bullets_as_well_as_titles(self):
        response = self.client.post(
            "/api/google/slides",
            json={"title": "Demo",
                  "slides": [{"title": "Results", "bullets": ["Fast", "Cheap"]}]})

        outline = response.json()["presentation"]["outline"]
        self.assertEqual(["Fast", "Cheap"], outline[0]["bullets"])


class AuthorizationTests(unittest.TestCase):
    """Reading the connection state must never open a browser."""

    def test_asking_for_a_service_does_not_start_a_sign_in(self):
        with mock.patch("google_auth_oauthlib.flow.InstalledAppFlow"
                        ".from_client_secrets_file") as flow:
            workspace_auth.get_google_service("drive", "v3")

        flow.assert_not_called()

    def test_state_is_reported_without_a_token(self):
        with mock.patch.object(workspace_auth, "has_client_secrets", return_value=True), \
             mock.patch.object(workspace_auth, "load_saved_credentials", return_value=None):
            state = workspace_auth.connection_state()
            self.assertEqual(workspace_auth.NEEDS_AUTHORIZATION, state["state"])
            self.assertFalse(workspace_auth.is_workspace_live())

    def test_the_gateway_reports_live_only_when_google_answers(self):
        gateway = WorkspaceGateway()

        with mock.patch.object(workspace_auth, "connection_state",
                               return_value={"state": workspace_auth.LIVE,
                                             "detail": "Connected."}), \
             mock.patch.object(WorkspaceGateway, "account_email", return_value="me@x.com"):
            status = gateway.get_status()

        self.assertTrue(status["connected"])
        self.assertEqual("live", status["mode"])
        self.assertEqual("me@x.com", status["account"])
        self.assertEqual("online", status["services"]["slides"]["status"])


if __name__ == "__main__":
    unittest.main()
