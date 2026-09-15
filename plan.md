# VAVE Upgrade Plan

Last updated: 2026-09-15
Baseline: Stage 11 complete, 702 tests green, HEAD `af592c3`, repo `sohail-bhai/VAVE-main`.

Decisions (from user):
- Mobile client: **PWA served by the existing FastAPI API** (no new toolchain).
- CI: **GitHub Actions on a Windows runner**.
- Smart home: **Home Assistant** bridge.
- Session scope: work through phases sequentially, as much as possible.

---

## Phase 0 — Quick Fixes, Docs Drift, CI (half day)

- [x] 0.1 **Unify low-disk threshold.** `gui/pages/home_page.py` warns at `< 7.0` GB
      while README/context.md claim `< 15 GB`. Make it configurable
      (`disk_warning_gb`, default 15) in `DEFAULT_CONFIG` + `config.json`, read it
      in the GUI.
- [x] 0.2 **TokenBucket clock-jump guard.** `assistant/api/auth.py` now uses
      `time.time()` (needed for persistence). An NTP step backwards makes elapsed
      negative and freezes the bucket. Clamp elapsed to `>= 0` in both the
      store-backed and in-memory paths.
- [x] 0.3 **Prune stale rate-limit buckets.** `rate_limit_buckets` grows forever.
      On each `update_rate_limit`, opportunistically delete rows untouched for
      more than 1 hour. Cheap, no timer thread.
- [x] 0.4 **Fix docs drift.** `AGENTS.md` and `docs/control-plane.md` still list
      "tokens never expire" and "rate limit buckets reset when the process
      restarts" as known gaps — both were fixed in Waves 5/6A and Stage 6A.
      Update the limitations lists to reflect current truth.
- [x] 0.5 **GitHub Actions CI.** `.github/workflows/ci.yml`: `windows-latest`,
      `pip install -r requirements.txt`, `python -m compileall`, then
      `python -m unittest discover -s tests`. Runs on push + PR.

Verification: full test suite green locally, CI green on GitHub.

## Phase 1 — Command Intelligence Completion (1 day)

- [ ] 1.1 **Make `command_shortcuts` real.** Table exists (migration 0018) but
      nothing reads it. Add store methods `save_command_shortcut`,
      `get_command_shortcut`, `list_command_shortcuts`, `delete_command_shortcut`.
- [ ] 1.2 **Voice shortcut creation.** "when I say <trigger> do <command>" /
      "create a shortcut" routed early in `commands.py` before other matching.
      Conflicts with built-in patterns are refused.
- [ ] 1.3 **Shortcut use.** Before the AI fallback, check exact-match shortcut
      triggers and expand to the stored command.
- [ ] 1.4 **Expose usage data.** `GET /api/commands/frequent` (top N patterns)
      and `GET /api/commands/shortcuts` + DELETE endpoint.
- [ ] 1.5 **GUI suggestion chips from real usage.** HomePage chips come from
      `get_frequent_commands()` instead of the static list.

Verification: new unit tests for shortcut save/expand/conflict + frequent
endpoint; existing suite stays green.

## Phase 2 — Mobile PWA Client (3-5 days)

- [ ] 2.1 **Static serving.** FastAPI mounts `mobile/` at `/m` (html, css, js,
      manifest, service worker). No build step, plain files.
- [ ] 2.2 **Login + pairing.** Enter device token (or scan QR from
      `POST /api/pair` output). Token stored in localStorage, used as Bearer.
- [ ] 2.3 **Task submission.** Text goal input → `POST /api/tasks`, show
      steps and live progress via `GET /api/events/stream` (SSE).
- [ ] 2.4 **Approvals.** Pending approvals list + approve/deny buttons
      (existing approval endpoints).
- [ ] 2.5 **Notifications.** Persistent notification list, mark-read, badge
      count. Live updates over SSE `/ws/notifications` fallback.
- [ ] 2.6 **Installable.** `manifest.webmanifest` + minimal service worker
      (app shell cache only — never cache API responses).
- [ ] 2.7 **QR pairing flow on desktop.** GUI/CLI command shows a QR
      (pairing code + host) so the phone can pair without typing tokens.
- [ ] 2.8 **Docs.** `docs/mobile.md` with pairing and usage instructions.

Verification: manual test over LAN with a real phone; API tests for any new
endpoints; AGENTS.md updated with `mobile/` map entry.

## Phase 3 — Reliability & Ops (1-2 days)

- [ ] 3.1 **Background health sweep.** Agent/device health is only swept on
      read. Add a repeating task: asyncio task (60s) in the API server lifespan;
      a daemon thread in CLI/GUI entry points that calls the same sweep.
- [ ] 3.2 **Secrets backup runbook.** `data/secret.key` loss = all credentials
      gone. Add `python -m assistant.secrets_backup export|verify` writing an
      encrypted (passphrase-protected) bundle of key + vault, plus docs on
      restore. Restore stays manual and explicit.
- [ ] 3.3 **Audit view in GUI.** Small page/modal reading the last N
      `task_journal` entries and recent audit events, so the user can see what
      VAVE did without opening the JSONL file.

Verification: unit tests for sweep cadence and backup round-trip
(encrypt → verify → restore into temp dir); suite green.

## Phase 4 — Home Assistant Bridge (2-3 days)

- [ ] 4.1 **Adapter.** `assistant/home.py` talking to the HA REST API
      (`/api/states`, `/api/services`) with the HA long-lived token stored as
      `secret://homeassistant` (capability `home.*`).
- [ ] 4.2 **Capabilities + policy.** `home.read` (safe), `home.control`
      (sensitive), risk-tiered in `capabilities.py`; guard classification.
- [ ] 4.3 **Brain tools.** `list_home_devices`, `get_device_state`,
      `control_device` (on/off/brightness/temperature targets).
- [ ] 4.4 **Voice routes.** "turn off the bedroom light", "what's the
      temperature in the living room" through the structured router.
- [ ] 4.5 **API endpoints.** `GET /api/home/devices`, `POST /api/home/control`
      going through the policy engine like every other capability.

Verification: unit tests against a mocked HA transport; manual test against a
live HA instance by the user.

## Phase 5 — Packaging & Distribution (1 day)

- [ ] 5.1 `pyproject.toml` with metadata, version, pinned dependencies.
- [ ] 5.2 Console entry point `vave` (`vave gui`, `vave serve`, `vave once`).
- [ ] 5.3 `pip install .` works in a clean venv; README setup section updated.
- [ ] 5.4 Optional: PyInstaller one-file build script (documented, not CI).

## Stretch (not scheduled)

- Complete the anchored-regex router for remaining substring commands.
- Coverage measurement in CI (`coverage run -m unittest`).
- ntfy.sh push bridge so mobile notifications work with no connected client.
- Agent-owned credentials (currently agents call through a paired device).
- PostgreSQL-backed store variant (only `store.py` would change).

---

## Progress Log

| Date | Entry |
| --- | --- |
| 2026-09-15 | Plan created. Phase 0 started. |
| 2026-09-15 | Phase 0 complete: disk warning configurable (default 15 GB), TokenBucket clock clamp, stale bucket pruning, docs drift fixed, CI workflow added. 704 tests green. |
