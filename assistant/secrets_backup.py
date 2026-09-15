"""Encrypted backup of the credential vault.

`data/secret.key` is deliberately kept outside the database, which means a
lost key file is a lost vault: every stored credential becomes unreadable.
This tool exports the key together with the encrypted secret rows into one
passphrase-protected bundle, so a reinstall or a dead drive is recoverable.

Nothing here ever decrypts a secret value. The rows travel in their stored
(encrypted) form; the bundle only re-keys the package.

    python -m assistant.secrets_backup export --out backup.vault --passphrase "..."
    python -m assistant.secrets_backup verify --in backup.vault --passphrase "..."
    python -m assistant.secrets_backup restore --in backup.vault --passphrase "..." --yes

`restore` refuses to run without `--yes`, and refuses to replace an existing
key without `--force`. See docs/secrets-backup.md for the runbook.

In tests, pass data_dir (or set VAVE_DATA_DIR) to keep the real data
directory untouched.
"""

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

BACKUP_VERSION = 1


def _passphrase_key(passphrase):
    from assistant.control import secrets as secrets_module
    return secrets_module._coerce_key(passphrase)


def resolve_data_dir(data_dir=None):
    """The data directory this backup reads from or writes to."""
    override = data_dir or os.environ.get("VAVE_DATA_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "data"


def export_bundle(out_path, passphrase, data_dir=None):
    """Bundle secret.key plus the encrypted secret rows into one vault file."""
    base = resolve_data_dir(data_dir)
    key_path = base / "secret.key"
    if not key_path.exists():
        raise FileNotFoundError(f"No key file at {key_path}; nothing to back up.")

    from assistant.control.store import ControlStore
    store = ControlStore(base / "control.db")
    try:
        rows = store.list_secrets()
    finally:
        store.close()

    bundle = {
        "version": BACKUP_VERSION,
        "exported_at": time.time(),
        "key": base64.b64encode(key_path.read_bytes()).decode("ascii"),
        "secrets": [
            {
                "name": row["name"],
                "ciphertext": row["ciphertext"],
                "description": row.get("description", "") or "",
                "allowed_capabilities": row.get("allowed_capabilities", "") or "",
                "created_at": row.get("created_at", 0.0),
                "updated_at": row.get("updated_at", 0.0),
            }
            for row in rows
        ],
    }
    payload = Fernet(_passphrase_key(passphrase)).encrypt(
        json.dumps(bundle).encode("utf-8"))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(payload)
    return {"path": str(out), "secrets": len(rows)}


def _read_bundle(bundle_path, passphrase):
    try:
        payload = Fernet(_passphrase_key(passphrase)).decrypt(
            Path(bundle_path).read_bytes())
    except InvalidToken as error:
        raise ValueError("Wrong passphrase, or the bundle is corrupt.") from error
    try:
        bundle = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("The bundle is corrupt.") from error
    if not isinstance(bundle, dict) or bundle.get("version") != BACKUP_VERSION:
        raise ValueError("Unsupported bundle version.")
    if "key" not in bundle or "secrets" not in bundle:
        raise ValueError("The bundle is corrupt.")
    return bundle


def verify_bundle(bundle_path, passphrase):
    """Decrypt and describe a bundle. Never writes anything."""
    bundle = _read_bundle(bundle_path, passphrase)
    return {
        "version": bundle["version"],
        "exported_at": bundle.get("exported_at", 0.0),
        "secrets": len(bundle["secrets"]),
        "names": sorted(row["name"] for row in bundle["secrets"]),
        "scopes": {row["name"]: row.get("allowed_capabilities", "")
                   for row in bundle["secrets"]},
    }


def restore_bundle(bundle_path, passphrase, data_dir=None, yes=False, force=False):
    """Write the key and secret rows back. Explicit flags only, no surprises."""
    if not yes:
        raise PermissionError("Refusing to overwrite credentials without --yes.")
    bundle = _read_bundle(bundle_path, passphrase)
    base = resolve_data_dir(data_dir)
    key_path = base / "secret.key"
    if key_path.exists() and not force:
        raise FileExistsError(
            f"A key already exists at {key_path}; pass --force to replace it.")

    base.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(base64.b64decode(bundle["key"].encode("ascii")))
    try:
        import stat
        key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

    from assistant.control.store import ControlStore
    store = ControlStore(base / "control.db")
    try:
        restored = 0
        for row in bundle["secrets"]:
            store.save_secret(
                name=row["name"],
                ciphertext=row["ciphertext"],
                description=row.get("description", ""),
                allowed_capabilities=row.get("allowed_capabilities", ""),
                updated_at=row.get("updated_at"),
            )
            restored += 1
    finally:
        store.close()
    return {"path": str(key_path), "secrets": restored}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Back up or restore the VAVE credential vault.")
    sub = parser.add_subparsers(dest="command", required=True)

    exp = sub.add_parser("export", help="Write an encrypted bundle of the key and vault.")
    exp.add_argument("--out", required=True)
    exp.add_argument("--passphrase", required=True)
    exp.add_argument("--data-dir", default="")

    ver = sub.add_parser("verify", help="Decrypt and describe a bundle without writing.")
    ver.add_argument("--in", dest="bundle", required=True)
    ver.add_argument("--passphrase", required=True)

    res = sub.add_parser("restore", help="Write the key and vault back from a bundle.")
    res.add_argument("--in", dest="bundle", required=True)
    res.add_argument("--passphrase", required=True)
    res.add_argument("--data-dir", default="")
    res.add_argument("--yes", action="store_true",
                     help="Required: acknowledge overwriting credentials.")
    res.add_argument("--force", action="store_true",
                     help="Required when a key file already exists.")

    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            result = export_bundle(args.out, args.passphrase, args.data_dir or None)
            print(f"Exported {result['secrets']} secret(s) to {result['path']}.")
        elif args.command == "verify":
            info = verify_bundle(args.bundle, args.passphrase)
            names = ", ".join(info["names"]) if info["names"] else "(none)"
            print(f"Bundle v{info['version']}: {info['secrets']} secret(s): {names}.")
        elif args.command == "restore":
            result = restore_bundle(args.bundle, args.passphrase,
                                    args.data_dir or None, yes=args.yes, force=args.force)
            print(f"Restored {result['secrets']} secret(s); key at {result['path']}.")
    except (FileNotFoundError, ValueError, PermissionError, FileExistsError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
