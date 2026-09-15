"""Unit tests for the encrypted secrets backup tool."""

import unittest
from pathlib import Path
import tempfile

from assistant import secrets_backup
from assistant.control.secrets import SecretStore
from assistant.control.store import ControlStore


class SecretsBackupTests(unittest.TestCase):
    def setUp(self):
        from cryptography.fernet import Fernet

        self.dir1 = Path(tempfile.mkdtemp(prefix="vave-backup-src-"))
        self.dir2 = Path(tempfile.mkdtemp(prefix="vave-backup-dst-"))
        self.bundle = self.dir1 / "backup.vault"

        # A self-contained vault: key file beside the database, like production.
        key = Fernet.generate_key()
        (self.dir1 / "secret.key").write_bytes(key)
        store = ControlStore(self.dir1 / "control.db")
        try:
            vault = SecretStore(store, key=key)
            vault.put("email_app_password", "hunter2",
                      description="mail", allowed_capabilities="google.gmail.*")
            vault.put("telegram_bot_token", "tok123",
                      allowed_capabilities="telegram.*")
        finally:
            store.close()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir1, ignore_errors=True)
        shutil.rmtree(self.dir2, ignore_errors=True)

    def test_export_verify_restore_roundtrip(self):
        result = secrets_backup.export_bundle(str(self.bundle), "correct horse", str(self.dir1))
        self.assertEqual(2, result["secrets"])
        self.assertTrue(self.bundle.exists())

        info = secrets_backup.verify_bundle(str(self.bundle), "correct horse")
        self.assertEqual(2, info["secrets"])
        self.assertEqual(["email_app_password", "telegram_bot_token"], info["names"])
        self.assertEqual("google.gmail.*", info["scopes"]["email_app_password"])

        with self.assertRaises(ValueError):
            secrets_backup.verify_bundle(str(self.bundle), "wrong horse")

        restored = secrets_backup.restore_bundle(
            str(self.bundle), "correct horse", str(self.dir2), yes=True)
        self.assertEqual(2, restored["secrets"])

        # The restored key unlocks the restored rows in the new directory.
        key = (self.dir2 / "secret.key").read_bytes()
        store = ControlStore(self.dir2 / "control.db")
        try:
            vault = SecretStore(store, key=key)
            self.assertEqual("hunter2",
                             vault.reveal("email_app_password", capability="google.gmail.send"))
            with self.assertRaises(PermissionError):
                vault.reveal("email_app_password", capability="telegram.send")
        finally:
            store.close()

    def test_restore_requires_explicit_flags(self):
        secrets_backup.export_bundle(str(self.bundle), "pw", str(self.dir1))
        with self.assertRaises(PermissionError):
            secrets_backup.restore_bundle(str(self.bundle), "pw", str(self.dir2), yes=False)

    def test_restore_will_not_clobber_a_key(self):
        secrets_backup.export_bundle(str(self.bundle), "pw", str(self.dir1))
        (self.dir2 / "secret.key").write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            secrets_backup.restore_bundle(str(self.bundle), "pw", str(self.dir2), yes=True)

    def test_export_without_key_fails_loudly(self):
        with self.assertRaises(FileNotFoundError):
            secrets_backup.export_bundle(str(self.dir2 / "x.vault"), "pw", str(self.dir2))

    def test_cli_roundtrip(self):
        self.assertEqual(
            0, secrets_backup.main(["export", "--out", str(self.bundle),
                                    "--passphrase", "pw", "--data-dir", str(self.dir1)]))
        self.assertEqual(
            0, secrets_backup.main(["verify", "--in", str(self.bundle),
                                    "--passphrase", "pw"]))
        self.assertEqual(
            1, secrets_backup.main(["verify", "--in", str(self.bundle),
                                    "--passphrase", "nope"]))
        self.assertEqual(
            0, secrets_backup.main(["restore", "--in", str(self.bundle),
                                    "--passphrase", "pw", "--data-dir", str(self.dir2),
                                    "--yes"]))
        self.assertEqual(
            1, secrets_backup.main(["restore", "--in", str(self.bundle),
                                    "--passphrase", "pw", "--data-dir", str(self.dir2),
                                    "--yes"]))


if __name__ == "__main__":
    unittest.main()
