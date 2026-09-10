"""Tests for the secret store.

The rule these protect: an agent gets `secret://name`, never the value. The
value appears only inside the control plane, at the moment a tool runs.
"""

import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from assistant.control.secrets import (
    KEY_ENVIRONMENT_VARIABLE,
    SecretNotFound,
    SecretStore,
    load_key,
)
from assistant.control.service import ControlPlane
from assistant.control.store import ControlStore


class SecretTestCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(prefix="vave-secret-test-")
        self.store = ControlStore(Path(self.tempdir) / "control.db")
        self.key = Fernet.generate_key()
        self.secrets = SecretStore(self.store, key=self.key)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tempdir, ignore_errors=True)


class KeyTests(SecretTestCase):
    def test_a_key_file_is_created_for_owner_eyes_only(self):
        path = Path(self.tempdir) / "secret.key"

        key = load_key(path)

        self.assertTrue(path.exists())
        self.assertEqual(key, path.read_bytes())
        if sys.platform != "win32":
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(0, mode & (stat.S_IRWXG | stat.S_IRWXO))

    def test_an_existing_key_is_reused(self):
        path = Path(self.tempdir) / "secret.key"

        self.assertEqual(load_key(path), load_key(path))

    def test_the_environment_wins_over_the_file(self):
        path = Path(self.tempdir) / "secret.key"
        load_key(path)
        os.environ[KEY_ENVIRONMENT_VARIABLE] = "a passphrase, not a fernet key"
        self.addCleanup(os.environ.pop, KEY_ENVIRONMENT_VARIABLE, None)

        key = load_key(path)

        self.assertNotEqual(path.read_bytes(), key)
        Fernet(key)     # a passphrase is still turned into a usable key


class StorageTests(SecretTestCase):
    def test_a_stored_secret_comes_back_when_the_plane_asks(self):
        self.secrets.put("email_app_password", "hunter2")

        self.assertEqual("hunter2", self.secrets.reveal("email_app_password"))

    def test_the_database_holds_no_readable_value(self):
        self.secrets.put("email_app_password", "hunter2")

        row = self.store.get_secret("email_app_password")

        self.assertNotIn("hunter2", row["ciphertext"])

    def test_listing_shows_names_and_never_values(self):
        self.secrets.put("email_app_password", "hunter2", description="Gmail")

        listed = self.secrets.list()

        self.assertEqual(["email_app_password"], [item["name"] for item in listed])
        self.assertEqual("secret://email_app_password", listed[0]["reference"])
        self.assertNotIn("hunter2", str(listed))

    def test_a_secret_can_be_replaced(self):
        self.secrets.put("token", "old")

        self.secrets.put("token", "new")

        self.assertEqual("new", self.secrets.reveal("token"))

    def test_a_deleted_secret_is_gone(self):
        self.secrets.put("token", "value")

        self.secrets.delete("token")

        self.assertFalse(self.secrets.has("token"))
        with self.assertRaises(SecretNotFound):
            self.secrets.reveal("token")

    def test_an_unknown_secret_is_reported_clearly(self):
        with self.assertRaises(SecretNotFound):
            self.secrets.reveal("nothing")

    def test_a_secret_needs_a_name(self):
        with self.assertRaises(ValueError):
            self.secrets.put("", "value")

    def test_the_wrong_key_cannot_read_the_value(self):
        self.secrets.put("token", "value")

        other = SecretStore(self.store, key=Fernet.generate_key())

        with self.assertRaises(SecretNotFound):
            other.reveal("token")

    def test_secrets_survive_a_reopen(self):
        self.secrets.put("token", "value")
        self.store.close()

        store = ControlStore(Path(self.tempdir) / "control.db")
        self.addCleanup(store.close)

        self.assertEqual("value", SecretStore(store, key=self.key).reveal("token"))


class ResolutionTests(SecretTestCase):
    def setUp(self):
        super().setUp()
        self.secrets.put("email_app_password", "hunter2")

    def test_a_reference_in_a_string_is_resolved(self):
        self.assertEqual("hunter2",
                         self.secrets.resolve("secret://email_app_password"))

    def test_a_reference_inside_text_is_resolved(self):
        resolved = self.secrets.resolve("login with secret://email_app_password now")

        self.assertEqual("login with hunter2 now", resolved)

    def test_tool_arguments_are_resolved_as_a_whole(self):
        arguments = {"to": "someone@example.com",
                     "password": "secret://email_app_password",
                     "attachments": ["secret://email_app_password"]}

        resolved = self.secrets.resolve(arguments)

        self.assertEqual("hunter2", resolved["password"])
        self.assertEqual(["hunter2"], resolved["attachments"])
        self.assertEqual("someone@example.com", resolved["to"])

    def test_text_without_references_is_untouched(self):
        self.assertEqual("nothing here", self.secrets.resolve("nothing here"))

    def test_an_unknown_reference_is_refused_rather_than_left_in_place(self):
        with self.assertRaises(SecretNotFound):
            self.secrets.resolve("secret://not_a_thing")

    def test_references_can_be_listed_without_resolving_them(self):
        found = self.secrets.references(
            {"password": "secret://email_app_password"})

        self.assertEqual(["email_app_password"], found)


class ResolveSettingTests(SecretTestCase):
    """resolve_setting must never leak the reference: an outbound integration
    that builds a URL from `secret://...` gets a permanent 404."""

    def setUp(self):
        super().setUp()
        self.secrets.put("telegram_bot_token", "12345:realtoken")

    def test_a_known_reference_resolves_to_its_value(self):
        self.assertEqual(
            "12345:realtoken",
            self.secrets.resolve_setting("secret://telegram_bot_token"))

    def test_an_unknown_reference_yields_the_default_not_the_reference(self):
        # The whole point: a failed lookup must not return "secret://..." to a
        # caller that would then build https://api.telegram.org/botsecret://...
        self.assertEqual("", self.secrets.resolve_setting("secret://missing"))
        self.assertEqual(
            "fallback",
            self.secrets.resolve_setting("secret://missing", default="fallback"))

    def test_a_plain_value_passes_straight_through(self):
        self.assertEqual("12345:plain",
                         self.secrets.resolve_setting("12345:plain"))

    def test_empty_and_non_string_are_left_alone(self):
        self.assertEqual("", self.secrets.resolve_setting(""))
        self.assertIsNone(self.secrets.resolve_setting(None))


class RedactionTests(SecretTestCase):
    def test_a_leaked_value_is_replaced_by_its_reference(self):
        self.secrets.put("email_app_password", "hunter2")

        self.assertEqual("logging in with secret://email_app_password",
                         self.secrets.redact("logging in with hunter2"))

    def test_text_with_nothing_to_hide_is_unchanged(self):
        self.secrets.put("token", "value")

        self.assertEqual("all fine", self.secrets.redact("all fine"))

    def test_the_timeline_never_carries_a_value(self):
        plane = ControlPlane(store=self.store)
        plane.secrets = self.secrets
        self.secrets.put("email_app_password", "hunter2")

        plane.record("Sent mail using hunter2")

        message = plane.list_events(limit=1)[0].message
        self.assertNotIn("hunter2", message)
        self.assertIn("secret://email_app_password", message)


class ConfigImportTests(SecretTestCase):
    def test_credentials_move_out_of_config_and_leave_references(self):
        config = {"telegram_bot_token": "12345:abcdef",
                  "email_app_password": "hunter2"}

        moved = self.secrets.import_from_config(
            config_get=lambda key, default="": config.get(key, default),
            config_set=lambda key, value: config.__setitem__(key, value))

        self.assertEqual(["telegram_bot_token", "email_app_password"], moved)
        self.assertEqual("secret://telegram_bot_token", config["telegram_bot_token"])
        self.assertEqual("12345:abcdef", self.secrets.reveal("telegram_bot_token", capability="telegram.send"))

    def test_an_empty_setting_is_left_alone(self):
        config = {"telegram_bot_token": "", "email_app_password": ""}

        moved = self.secrets.import_from_config(
            config_get=lambda key, default="": config.get(key, default),
            config_set=lambda key, value: config.__setitem__(key, value))

        self.assertEqual([], moved)

    def test_importing_twice_does_not_re_import_a_reference(self):
        config = {"telegram_bot_token": "12345:abcdef", "email_app_password": ""}
        get = lambda key, default="": config.get(key, default)
        put = lambda key, value: config.__setitem__(key, value)

        self.secrets.import_from_config(config_get=get, config_set=put)
        moved = self.secrets.import_from_config(config_get=get, config_set=put)

        self.assertEqual([], moved)
        self.assertEqual("12345:abcdef", self.secrets.reveal("telegram_bot_token", capability="telegram.send"))


class ScopedSecretTests(SecretTestCase):
    def test_scoped_secret_resolves_for_matching_capability(self):
        self.secrets.put("db_password", "pass123", allowed_capabilities="database.*,sql.*")
        self.assertEqual("pass123", self.secrets.reveal("db_password", capability="database.query"))
        resolved = self.secrets.resolve("connect secret://db_password", capability="sql.execute")
        self.assertEqual("connect pass123", resolved)

    def test_scoped_secret_refused_for_unmatching_capability(self):
        self.secrets.put("db_password", "pass123", allowed_capabilities="database.*,sql.*")
        with self.assertRaises(PermissionError):
            self.secrets.reveal("db_password", capability="shell.run")
        with self.assertRaises(PermissionError):
            self.secrets.resolve("secret://db_password", capability="browser.navigate")
        with self.assertRaises(PermissionError):
            self.secrets.resolve("secret://db_password", capability="")

    def test_unscoped_secret_resolves_for_any_capability(self):
        self.secrets.put("public_note", "hello", allowed_capabilities="")
        self.assertEqual("hello", self.secrets.reveal("public_note", capability="shell.run"))
        self.assertEqual("hello", self.secrets.reveal("public_note"))
        self.assertEqual("hello", self.secrets.resolve("secret://public_note"))


if __name__ == "__main__":
    unittest.main()
