# Secrets Backup Runbook

## Why this exists

VAVE encrypts every stored credential with a key in `data/secret.key`, kept
outside the database on purpose. The trade: if that file is lost (dead drive,
fresh install, accidental delete), the whole vault is unreadable even though
`control.db` still holds the rows. Back up the key plus the vault now, while
everything works.

## Back up

```powershell
venv\Scripts\python.exe -m assistant.secrets_backup export --out C:\backups\vave-2026-09-15.vault --passphrase "a long phrase you will remember"
```

What you get: one file holding `secret.key` and every encrypted secret row,
itself encrypted with your passphrase. The secret *values* are never
decrypted during export — they travel in their stored form.

Store the bundle somewhere the computer is not: a USB stick, a second
machine, a password manager's file attachment. losing the bundle AND the key
is the same as having no backup.

## Check a bundle

```powershell
venv\Scripts\python.exe -m assistant.secrets_backup verify --in C:\backups\vave-2026-09-15.vault --passphrase "..."
```

This decrypts and lists secret names and their capability scopes. It writes
nothing. A wrong passphrase fails cleanly with "Wrong passphrase".

## Restore after a reinstall (or a lost key)

1. Install VAVE fresh and run it once so `data/` exists (do not pair anything
   yet unless you want to).
2. Restore — this overwrites credentials, so it needs `--yes`, and it needs
   `--force` if a key file already exists:
   ```powershell
   venv\Scripts\python.exe -m assistant.secrets_backup restore --in C:\backups\vave-2026-09-15.vault --passphrase "..." --yes --force
   ```
3. Verify: `verify` the bundle again, then start VAVE and run one command
   that needs a credential (e.g. "read my emails" for Gmail). If it works,
   the restore worked.

## Rules the tool enforces

- `export` fails loudly when there is no key file (nothing to back up).
- `restore` refuses without `--yes`, and refuses to clobber an existing key
  without `--force`. There is no silent overwrite path.
- A bundle only restores into a VAVE whose code understands its version
  (currently v1).
