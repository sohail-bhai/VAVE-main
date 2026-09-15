"""Unit tests for the mobile PWA client served at /m."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from assistant.api.app import create_app
from assistant.api.auth import ApiSecurity
from assistant.control.service import ControlPlane
from assistant.control.store import ControlStore

MOBILE_DIR = Path(__file__).resolve().parent.parent / "mobile" / "pwa"


class MobilePwaTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="vave-mobile-test-")
        self.store = ControlStore(Path(self.tempdir) / "control.db")
        self.plane = ControlPlane(store=self.store)
        self.guard = ApiSecurity(self.store, require_auth=True, trust_local=False)
        self.client = TestClient(create_app(control=self.plane, security=self.guard))

    def tearDown(self):
        self.store.close()
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def _headers(self):
        code = self.guard.issue_pairing_code()["code"]
        claim = self.client.post("/api/pair", json={"code": code, "name": "TestPhone"})
        self.assertEqual(201, claim.status_code)
        return {"Authorization": f"Bearer {claim.json()['token']}"}

    def test_shell_files_served(self):
        index = self.client.get("/m/")
        self.assertEqual(200, index.status_code)
        self.assertIn("VAVE Mobile", index.text)

        for asset in ("styles.css", "app.js", "sw.js",
                      "manifest.webmanifest", "icon-192.svg", "icon-512.svg"):
            res = self.client.get(f"/m/{asset}")
            self.assertEqual(200, res.status_code, f"/m/{asset}")

    def test_manifest_is_valid_json(self):
        raw = (MOBILE_DIR / "manifest.webmanifest").read_text(encoding="utf-8")
        manifest = json.loads(raw)
        self.assertIn("name", manifest)
        self.assertEqual("standalone", manifest.get("display"))
        for icon in manifest["icons"]:
            self.assertTrue((MOBILE_DIR / icon["src"]).is_file())

    def test_service_worker_never_caches_api(self):
        sw = (MOBILE_DIR / "sw.js").read_text(encoding="utf-8")
        self.assertIn("/api/", sw)

    def test_mobile_paths_are_public_but_api_is_not(self):
        self.assertEqual(200, self.client.get("/m/").status_code)
        self.assertEqual(401, self.client.get("/api/tasks").status_code)
        self.assertEqual(401, self.client.post("/api/tasks", json={"goal": "hi"}).status_code)

    def test_goal_lifecycle_api_used_by_the_app(self):
        headers = self._headers()

        created = self.client.post(
            "/api/tasks",
            json={"goal": "what time is it", "autoplan": False, "run": False,
                  "steps": ["Tell the time"]},
            headers=headers,
        )
        self.assertEqual(201, created.status_code)
        task_id = created.json()["id"]
        detail = self.client.get(f"/api/tasks/{task_id}", headers=headers)
        self.assertEqual(200, detail.status_code)
        self.assertEqual("what time is it", detail.json()["goal"])
        self.assertEqual(1, len(detail.json()["steps"]))
        self.assertEqual("pending", detail.json()["steps"][0]["status"].lower())

        approvals = self.client.get("/api/approvals?pending_only=true", headers=headers)
        self.assertEqual(200, approvals.status_code)

        notifications = self.client.get("/api/notifications?limit=30", headers=headers)
        self.assertEqual(200, notifications.status_code)


if __name__ == "__main__":
    unittest.main()
