"""Tests for live Google Workspace OAuth (Track 2).

The consent screen itself needs a person and a Google Cloud project, so these
tests pin everything around it with fakes: the offline/consent parameters,
encrypted vault persistence, file migration, refresh persistence, and the
calendar fallback chain. No network, no browser.
"""

import unittest
from unittest import mock


def _fake_creds(valid=True, expired=False, refresh_token="refresh-token"):
    creds = mock.MagicMock()
    creds.valid = valid
    creds.expired = expired
    creds.refresh_token = refresh_token
    creds.to_json.return_value = '{"token": "abc"}'
    return creds


def _fake_store(names=(), values=None):
    store = mock.MagicMock()
    saved = {}

    def put(name, value, description="", allowed_capabilities=""):
        saved[name] = value

    def has(name):
        return name in saved or name in names

    def reveal(name, capability=""):
        if name in saved:
            return saved[name]
        raise AssertionError(f"unexpected reveal: {name}")

    def delete(name):
        saved.pop(name, None)

    store.put.side_effect = put
    store.has.side_effect = has
    store.reveal.side_effect = reveal
    store.delete.side_effect = delete
    store._saved = saved
    if values:
        saved.update(values)
    return store


class AuthorizeTests(unittest.TestCase):
    def test_authorize_requests_offline_consent(self):
        from assistant.workspace import auth
        creds = _fake_creds()
        flow = mock.MagicMock()
        flow.run_local_server.return_value = creds
        flow_cls = mock.MagicMock()
        flow_cls.from_client_secrets_file.return_value = flow

        with mock.patch.object(auth, "has_client_secrets", return_value=True), \
             mock.patch("google_auth_oauthlib.flow.InstalledAppFlow", flow_cls), \
             mock.patch.object(auth, "_write_token") as mock_write, \
             mock.patch.object(auth, "_save_token_secret",
                               return_value=True) as mock_save:
            result = auth.authorize(open_browser=False)

        self.assertEqual("live", result["state"])
        _, kwargs = flow.run_local_server.call_args
        self.assertEqual("offline", kwargs.get("access_type"))
        self.assertEqual("consent", kwargs.get("prompt"))
        self.assertFalse(kwargs.get("open_browser", True))
        mock_write.assert_called_once_with(creds)
        mock_save.assert_called_once_with('{"token": "abc"}')

    def test_authorize_without_client_secrets_does_not_start_flow(self):
        from assistant.workspace import auth
        with mock.patch.object(auth, "has_client_secrets", return_value=False):
            result = auth.authorize()
        self.assertEqual("not_configured", result["state"])


class VaultTokenTests(unittest.TestCase):
    def test_vault_token_is_used_first(self):
        from assistant.workspace import auth
        creds = _fake_creds()
        store = _fake_store(values={auth.OAUTH_SECRET_NAME: '{"token": "abc"}'})
        with mock.patch.object(auth, "_get_secret_store", return_value=store), \
             mock.patch.object(auth, "_creds_from_json",
                               return_value=creds) as mock_parse:
            self.assertIs(creds, auth.load_saved_credentials())
        mock_parse.assert_called_once_with('{"token": "abc"}')

    def test_legacy_file_migrates_into_vault(self):
        import json
        import tempfile
        from pathlib import Path
        from assistant.workspace import auth
        creds = _fake_creds()
        with tempfile.TemporaryDirectory(prefix="vave-oauth-") as tmp:
            token_file = Path(tmp) / "token_workspace.json"
            token_file.write_text(json.dumps({"token": "abc"}),
                                  encoding="utf-8")
            store = _fake_store()
            parse = lambda token_json: creds if token_json else None
            with mock.patch.object(auth, "get_token_path",
                                   return_value=str(token_file)), \
                 mock.patch.object(auth, "_get_secret_store",
                                   return_value=store), \
                 mock.patch.object(auth, "_creds_from_json",
                                   side_effect=parse):
                self.assertIs(creds, auth.load_saved_credentials())
        self.assertIn(auth.OAUTH_SECRET_NAME, store._saved)

    def test_refresh_persists_to_vault(self):
        from assistant.workspace import auth
        creds = _fake_creds(valid=False, expired=True)
        store = _fake_store(values={auth.OAUTH_SECRET_NAME: '{"token": "old"}'})
        with mock.patch.object(auth, "_get_secret_store",
                               return_value=store), \
             mock.patch.object(auth, "_creds_from_json",
                               return_value=creds), \
             mock.patch.object(auth, "_write_token"):
            self.assertIs(creds, auth.load_saved_credentials())
        creds.refresh.assert_called_once()

    def test_disconnect_forgets_vault_and_files(self):
        import tempfile
        from pathlib import Path
        from assistant.workspace import auth
        with tempfile.TemporaryDirectory(prefix="vave-oauth-") as tmp:
            token_file = Path(tmp) / "token_workspace.json"
            token_file.write_text("{}", encoding="utf-8")
            store = _fake_store(values={auth.OAUTH_SECRET_NAME: "x"})
            with mock.patch.object(auth, "get_token_paths",
                                   return_value=[str(token_file)]), \
                 mock.patch.object(auth, "_get_secret_store",
                                   return_value=store), \
                 mock.patch.object(auth, "connection_state",
                                   return_value={"state": "x"}):
                auth.disconnect()
        self.assertNotIn(auth.OAUTH_SECRET_NAME, store._saved)
        self.assertFalse(token_file.exists())


class CalendarFallbackTests(unittest.TestCase):
    def test_workspace_credentials_win(self):
        import assistant.calendar_sync as sync
        creds = _fake_creds()
        service = object()
        with mock.patch("assistant.workspace.auth.load_saved_credentials",
                        return_value=creds), \
             mock.patch.object(sync, "build", return_value=service,
                               create=True) as mock_build:
            self.assertIs(service, sync.get_calendar_service())
        mock_build.assert_called_once_with("calendar", "v3",
                                           credentials=creds)

    def test_legacy_flow_survives_without_workspace(self):
        # No workspace creds and no token files anywhere: a spoken
        # explanation and None, never an exception or a browser.
        import assistant.calendar_sync as sync
        service = object()
        with mock.patch("assistant.workspace.auth.load_saved_credentials",
                        return_value=None), \
             mock.patch("os.path.exists", return_value=False), \
             mock.patch.object(sync, "speak") as mock_speak, \
             mock.patch.object(sync, "build", create=True) as mock_build:
            self.assertIsNone(sync.get_calendar_service())
        mock_speak.assert_called_once()
        mock_build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
