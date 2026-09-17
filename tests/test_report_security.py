"""Regression tests for report.md / error_report.md — Security domain.

Each class pins one finding ID so the report and the suite stay linked.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from assistant.api.app import create_app
from assistant.api.auth import ApiSecurity
from assistant.control.service import ControlPlane
from assistant.control.store import ControlStore


def _request(origin, server_host):
    req = mock.MagicMock()
    req.headers = {"origin": origin} if origin is not None else {}
    req.url.hostname = server_host
    return req


class Sec01PairingOriginTests(unittest.TestCase):
    """SEC-01: an evil.com tab must not mint pairing codes or claim them.

    The attack needs two ingredients: loopback trust (may_pair passes for a
    browser on this machine) and readable responses (wildcard CORS). The fix
    removes the wildcard and refuses cross-origin browser requests at the
    pairing endpoints entirely — even blind POSTs that expect no response.
    """

    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="vave-sec01-test-")
        self.store = ControlStore(Path(self.tempdir) / "control.db")

    def tearDown(self):
        if getattr(self, "store", None):
            self.store.close()
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def _client(self):
        plane = ControlPlane(store=self.store)
        guard = ApiSecurity(self.store, require_auth=True, trust_local=True)
        return TestClient(create_app(control=plane, security=guard)), guard

    def _paired_token(self, client, guard):
        code = guard.issue_pairing_code()["code"]
        res = client.post("/api/pair",
                          json={"code": code, "name": "SecPhone"})
        self.assertEqual(201, res.status_code)
        return res.json()["token"]

    def test_check_same_origin_allows_absent_origin(self):
        guard = ApiSecurity(self.store, require_auth=True, trust_local=True)
        guard.check_same_origin(_request(None, "127.0.0.1"))

    def test_check_same_origin_allows_same_origin(self):
        guard = ApiSecurity(self.store, require_auth=True, trust_local=True)
        guard.check_same_origin(_request("http://127.0.0.1:8765", "127.0.0.1"))
        guard.check_same_origin(_request("http://192.168.1.20:8765",
                                         "192.168.1.20"))

    def test_check_same_origin_allows_garbage_origin(self):
        guard = ApiSecurity(self.store, require_auth=True, trust_local=True)
        guard.check_same_origin(_request("not a url", "127.0.0.1"))

    def test_check_same_origin_refuses_evil_origin(self):
        guard = ApiSecurity(self.store, require_auth=True, trust_local=True)
        with self.assertRaises(PermissionError):
            guard.check_same_origin(_request("https://evil.example",
                                             "127.0.0.1"))

    def test_cross_origin_pair_code_refused(self):
        client, guard = self._client()
        token = self._paired_token(client, guard)
        auth = {"Authorization": f"Bearer {token}"}

        evil = client.post("/api/pair/code",
                           headers={**auth, "origin": "https://evil.example"})
        self.assertEqual(403, evil.status_code)

        same = client.post("/api/pair/code",
                           headers={**auth, "origin": "http://testserver"})
        self.assertEqual(200, same.status_code)

        plain = client.post("/api/pair/code", headers=auth)
        self.assertEqual(200, plain.status_code)

    def test_cross_origin_pair_claim_refused_without_consuming_code(self):
        client, guard = self._client()
        code = guard.issue_pairing_code()["code"]

        evil = client.post("/api/pair",
                           json={"code": code, "name": "EvilPhone"},
                           headers={"origin": "https://evil.example"})
        self.assertEqual(403, evil.status_code)

        # The code must still be valid: the refused request claimed nothing.
        retry = client.post("/api/pair",
                            json={"code": code, "name": "RealPhone"})
        self.assertEqual(201, retry.status_code)

    def test_no_wildcard_cors_on_cross_origin_read(self):
        client, _guard = self._client()
        res = client.get("/api/status",
                         headers={"origin": "https://evil.example"})
        self.assertNotIn("access-control-allow-origin", res.headers)

    def test_preflight_grants_nothing_cross_origin(self):
        client, _guard = self._client()
        res = client.options(
            "/api/status",
            headers={"origin": "https://evil.example",
                     "access-control-request-method": "POST"})
        self.assertNotIn("access-control-allow-origin", res.headers)


class Sec02SsrfRedirectTests(unittest.TestCase):
    """SEC-02: redirects are re-validated; credentials never cross hosts.

    Uses IP literals so no DNS (and no internet) is needed.
    """

    def _request(self, url):
        import urllib.request
        return urllib.request.Request(
            url, headers={"Authorization": "Bearer s3cret",
                          "Accept": "application/json"})

    def _redirect(self, request, newurl):
        import io
        from assistant.web_api import _CheckedRedirectHandler
        handler = _CheckedRedirectHandler()
        return handler.redirect_request(request, io.BytesIO(b""), 302,
                                        "Found", {}, newurl)

    def test_redirect_to_loopback_refused(self):
        from assistant.web_api import BlockedAddress
        req = self._request("http://93.184.216.34/start")
        with self.assertRaises(BlockedAddress):
            self._redirect(req, "http://127.0.0.1:8765/api/status")

    def test_redirect_to_metadata_service_refused(self):
        from assistant.web_api import BlockedAddress
        req = self._request("http://93.184.216.34/start")
        with self.assertRaises(BlockedAddress):
            self._redirect(req, "http://169.254.169.254/latest/meta-data/")

    def test_cross_host_redirect_strips_credentials(self):
        req = self._request("http://93.184.216.34/start")
        new = self._redirect(req, "http://93.184.216.35/other")
        names = [name.lower() for name in new.headers]
        self.assertNotIn("authorization", names)
        self.assertIn("accept", names)

    def test_same_host_redirect_keeps_credentials(self):
        req = self._request("http://93.184.216.34/start")
        new = self._redirect(req, "http://93.184.216.34/other")
        names = [name.lower() for name in new.headers]
        self.assertIn("authorization", names)

    def test_scheme_downgrade_strips_credentials(self):
        req = self._request("https://93.184.216.34/start")
        new = self._redirect(req, "http://93.184.216.34/other")
        names = [name.lower() for name in new.headers]
        self.assertNotIn("authorization", names)

    def test_initial_check_still_blocks_loopback(self):
        from assistant.web_api import BlockedAddress, check_address
        with self.assertRaises(BlockedAddress):
            check_address("http://127.0.0.1:8765/api/status")

    def test_redirect_chain_blocked_end_to_end(self):
        import threading
        import urllib.request
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from assistant.web_api import _open, BlockedAddress

        hits = []

        class Target(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                body = b'{"secret": true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        class Redirector(BaseHTTPRequestHandler):
            location = ""

            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", self.location)
                self.end_headers()

            def log_message(self, *args):
                pass

        target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
        redirector = ThreadingHTTPServer(("127.0.0.1", 0), Redirector)
        Redirector.location = (
            f"http://127.0.0.1:{target.server_port}/secret")
        threads = [
            threading.Thread(target=server.serve_forever, daemon=True)
            for server in (target, redirector)
        ]
        for thread in threads:
            thread.start()
        self.addCleanup(target.shutdown)
        self.addCleanup(target.server_close)
        self.addCleanup(redirector.shutdown)
        self.addCleanup(redirector.server_close)

        req = urllib.request.Request(
            f"http://127.0.0.1:{redirector.server_port}/go")
        with self.assertRaises(BlockedAddress):
            _open(req)
        self.assertEqual([], hits)


class Sec03HiddenSubfolderTests(unittest.TestCase):
    """SEC-03: every path segment counts, and uploads cannot land on secrets."""

    def setUp(self):
        import shutil
        import tempfile
        import assistant.files as files
        self.files = files
        self.root = Path(tempfile.mkdtemp(prefix="vave-sec03-test-")).resolve()
        self.shared = self.root / "Documents"
        (self.shared / ".git").mkdir(parents=True)
        (self.shared / ".git" / "config").write_text("secret-stuff")
        (self.shared / "notes.txt").write_text("hello")
        self._real_get_setting = files.get_setting
        files.get_setting = lambda key, default=None: (
            [str(self.shared)] if key == "file_shares"
            else self._real_get_setting(key, default))
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import shutil
        self.files.get_setting = self._real_get_setting
        shutil.rmtree(self.root, ignore_errors=True)

    def test_is_hidden_checks_all_segments(self):
        self.assertTrue(self.files._is_hidden(Path(".git/config")))
        self.assertTrue(self.files._is_hidden(Path("sub/.secrets/db.json")))
        self.assertTrue(self.files._is_hidden(Path("control.db")))
        self.assertFalse(self.files._is_hidden(Path("notes.txt")))
        self.assertFalse(self.files._is_hidden(Path("reports/q3.txt")))

    def test_dotfile_inside_subfolder_cannot_download(self):
        from assistant.files import FileAccessError
        with self.assertRaises(FileAccessError):
            self.files.resolve("Documents/.git/config")

    def test_plain_file_inside_subfolder_still_resolves(self):
        self.assertEqual(self.files.resolve("Documents/notes.txt"),
                         self.shared / "notes.txt")

    def test_upload_refuses_dotfiles(self):
        from io import BytesIO
        from assistant.files import FileAccessError
        with self.assertRaises(FileAccessError):
            self.files.save_upload("Documents", ".env", BytesIO(b"x=1"))
        self.assertFalse((self.shared / ".env").exists())

    def test_upload_refuses_protected_names(self):
        from io import BytesIO
        from assistant.files import FileAccessError
        with self.assertRaises(FileAccessError):
            self.files.save_upload("Documents", "control.db",
                                   BytesIO(b"evil"), overwrite=True)

    def test_upload_accepts_plain_names(self):
        from io import BytesIO
        entry = self.files.save_upload("Documents", "hello.txt",
                                       BytesIO(b"hi"))
        self.assertTrue((self.shared / "hello.txt").exists())
        self.assertEqual(entry["name"], "hello.txt")


class Sec04ProtectedReadTests(unittest.TestCase):
    """SEC-04: credentials and private keys are unreadable via tools."""

    def test_protected_names_recognized(self):
        from assistant.guard import is_protected_read_path
        for path in ("data/secret.key", "data\\SECRET.KEY", "control.db",
                     "/home/u/.ssh/id_rsa", "~/.ssh/id_ed25519",
                     "C:\\Users\\u\\.aws\\credentials", "keys/backup.pem"):
            with self.subTest(path=path):
                self.assertTrue(is_protected_read_path(path), path)

    def test_plain_paths_allowed(self):
        from assistant.guard import is_protected_read_path
        for path in ("README.md", "data/notes.txt", "assistant/commands.py",
                     "", "notes about secrets"):
            with self.subTest(path=path):
                self.assertEqual("", is_protected_read_path(path))

    def test_hard_deny_blocks_read_file_tool(self):
        from assistant.guard import _is_hard_denied
        denied, reason = _is_hard_denied(
            "read_file", {"path": "C:/Users/u/.ssh/id_rsa"})
        self.assertTrue(denied)
        self.assertIn("hard-denied", reason)
        denied, _reason = _is_hard_denied(
            "read_file", {"path": "README.md"})
        self.assertFalse(denied)

    def test_hard_deny_blocks_shell_exfiltration(self):
        from assistant.guard import _is_hard_denied
        denied, _reason = _is_hard_denied(
            "run_terminal_command", {"command": "type data\\secret.key"})
        self.assertTrue(denied)
        denied, _reason = _is_hard_denied(
            "run_terminal_command", {"command": "cat ~/.ssh/id_rsa"})
        self.assertTrue(denied)
        denied, _reason = _is_hard_denied(
            "run_terminal_command", {"command": "echo hello"})
        self.assertFalse(denied)

    def test_read_file_refuses_directly(self):
        import tempfile
        from pathlib import Path
        from assistant import system_tasks
        with tempfile.TemporaryDirectory(prefix="vave-sec04-") as tmp:
            secret = Path(tmp) / "secret.key"
            secret.write_text("TOPSECRET", encoding="utf-8")
            result = system_tasks.read_file(str(secret))
            self.assertIn("Refusing", result)
            self.assertNotIn("TOPSECRET", result)

            plain = Path(tmp) / "hello.txt"
            plain.write_text("hi", encoding="utf-8")
            self.assertEqual("hi", system_tasks.read_file(str(plain)))


if __name__ == "__main__":
    unittest.main()
