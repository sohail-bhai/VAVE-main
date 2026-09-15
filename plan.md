# VAVE Upgrade Plan — Agent Handoff Edition

This file is the single source of truth for the upgrade work. An agent who has
never seen the conversation that produced it must be able to pick it up and
execute a phase alone. Read sections 1-3 fully before touching code.

---

## 1. Current State

- Baseline commit: `18e5a21` on branch `sohail`, remote `origin`
  (`https://github.com/sohail-bhai/VAVE-main.git`).
- Stage 11 + plan Phases 0 and 1 are COMPLETE (see section 9).
- Test count: **714**, all green in the dev venv AND in a clean-install venv.
- CI: `.github/workflows/ci.yml` (windows-latest, Python 3.13) runs compileall,
  unittest, smoke test on every push. It was failing and is now expected green;
  always check the run after pushing (section 3.6).
- `context.md` holds the stage-by-stage history; `AGENTS.md` holds repo rules.
  This file holds the forward plan.
- Phases remaining, in order: **2 (Mobile PWA) -> 3 (Reliability & Ops) ->
  4 (Home Assistant) -> 5 (Packaging)**. Stretch items are unscheduled.

## 2. Environment Facts (READ FIRST — these bite)

1. **Always use the repo venv**: `venv\Scripts\python.exe ...`. The system
   Python has no project deps, and `pytest` is NOT installed anywhere — the
   suite is stdlib `unittest`.
2. Shell is **Windows PowerShell 5.1**: no `&&` (use `;` or `cmd1; if ($?) {cmd2}`),
   no `rg`, no `gh` CLI. Use `Select-String` for searching, or the Grep tool.
3. The API server binds `0.0.0.0:8765` by default (see `config.json`). For
   manual tests use `127.0.0.1` unless testing from a phone.
4. **Never** run `python main.py` bare — it starts the voice loop and blocks.
   Safe commands: `--smoke-test`, `--text "..." --no-speech`, `-m compileall`.
5. `data/notes.txt`, `data/control.db`, `data/secret.key`, `config.json` are
   USER STATE. Tests must use `VAVE_DATA_DIR` (see `tests/support.py`,
   `VaveTestCase`). Never overwrite them in real runs.
6. Emoji/mojibake in README headings is pre-existing GitHub-badges content;
   don't "fix" it as part of unrelated phases.
7. Mock-target lesson (cost an hour once): patch where a name is USED, not
   where it is defined. `commands.py` does `from assistant.system_tasks import
   tell_time`, so tests patch `assistant.commands.tell_time`.
8. Icon cache lesson: `gui/icons.py` caches CTkImages process-wide; Tk deletes
   the bitmaps when the root dies. `VaveDashboardApp.destroy()` calls
   `icons.reset_cache()` — any new window/test flow that destroys a root must
   keep that behavior.
9. Dependencies: pins must have wheels for Python 3.13 on Windows. Before
   changing `requirements.txt`, dry-run a clean install (section 3.5).

## 3. Standard Workflow (every phase, no exceptions)

1. **Read before writing**: the modules listed in the phase's "Read first".
2. **Implement** the tasks in order; keep changes small and consistent with
   the plain-Python module style of the repo.
3. **Write the automated tests** listed in the phase. New test files go in
   `tests/` and follow existing patterns (`VaveTestCase` when touching user
   data; fake transports like `FakeGitHubTransport` in `tests/test_developer_git.py`
   for external services).
4. **Verify locally**:
   ```powershell
   venv\Scripts\python.exe -m compileall -q main.py assistant gui vave_gui.py
   venv\Scripts\python.exe -m unittest tests.test_<new_module> -v      # fast, targeted
   venv\Scripts\python.exe -m unittest discover -s tests               # full, ~2-4 min
   ```
   The suite MUST be fully green before committing. Expected log noise:
   pygame banner, deliberate test exceptions ("listener is broken",
   "the phone is off", "Ollama is not running") — those are tests asserting
   error handling, not failures.
5. **CI-affecting changes** (requirements.txt, new imports of heavy packages,
   GUI lifecycle): dry-run a clean install before pushing (section 3.5).
6. **Commit** (conventional commits, matching history: `feat(scope): ...`,
   `fix(scope): ...`) and **push**:
   ```powershell
   git add <files>; git commit -m "feat(scope): summary

   - detail
   - detail"
   git push
   ```
   Never commit secrets, `venv/`, `data/`, or `logs/`.
7. **Check CI** (no gh CLI — use the REST API):
   ```powershell
   (Invoke-RestMethod "https://api.github.com/repos/sohail-bhai/VAVE-main/actions/runs?per_page=1").workflow_runs[0] | Select-Object status, conclusion, display_title
   ```
   Wait for `completed` + `success`. If it fails, download the log from the
   Actions tab in the browser (API log download needs admin rights).
8. **Update this file**: tick the boxes, add a Progress Log row, and update
   `context.md` with a short stage entry. The user then runs the phase's
   "User manual test" checklist.

### 3.5 Clean-install CI dry run

```powershell
$ci = "$env:TEMP\vave_ci_dry"; if (Test-Path $ci) { Remove-Item -Recurse -Force $ci }
python -m venv $ci
& "$ci\Scripts\python.exe" -m pip install --upgrade pip -q
& "$ci\Scripts\python.exe" -m pip install -r requirements.txt
& "$ci\Scripts\python.exe" -m compileall -q main.py assistant gui vave_gui.py
& "$ci\Scripts\python.exe" -m unittest discover -s tests
```
This is exactly what CI does. It caught two real bugs already (greenlet pin,
icon cache). Reuse the venv (don't delete) to keep pip's cache warm.

---

## 4. Phase 2 — Mobile PWA Client (NEXT UP)

**Goal**: a phone-friendly web app served by the existing FastAPI server at
`/m`. No new toolchain, no build step — plain HTML/CSS/JS files. The phone
pairs, submits goals, watches steps, answers approvals, reads notifications.

**Decisions already made by the user**: PWA approach (not Flutter/RN);
Home Assistant is Phase 4; keep everything behind existing auth.

### Read first
- `assistant/api/app.py` — `create_app`, the auth middleware (lines ~278-305),
  `OPEN_PATHS` (~line 220), tasks/approvals/notifications/events routes,
  `lifespan` (~line 242).
- `docs/control-plane.md` — endpoint semantics.
- `gui/theme.py` — the dashboard's dark palette, for visual consistency.

### Key API facts (verified at baseline)
- Auth: `Authorization: Bearer <token>` on every `/api/*` route except
  `OPEN_PATHS`. The middleware ALSO accepts `?token=<token>` query param —
  **EventSource cannot set headers, so the SSE stream must use the query param**.
- Pair from the phone: `POST /api/pair` with `{"code": "...", "name": "...",
  "kind": "phone"}`; codes come from `POST /api/pair/code` (or
  `python -m assistant.api --pair`).
- Create a task: `POST /api/tasks` with `{"goal": "...", "autoplan": true,
  "run": true}` (fields of `CreateTaskRequest`).
- Task detail/progress: `GET /api/tasks/{id}` (steps carry status).
- Approvals: `GET /api/approvals?pending_only=true`;
  `POST /api/approvals/{id}` with `{"approved": true|false}`.
- Notifications: `GET /api/notifications?limit=20`;
  `POST /api/notifications/{id}/read`.
- Live events: `GET /api/events/stream` (SSE, needs token).
- WebSockets exist (`/ws/events`, `/ws/notifications`) with 25s pings —
  use SSE first, it is simpler and already authenticated.

### Tasks

- [x] ~~2.1 **Serve the app.** Create `mobile/` at repo root with `index.html`,
      `styles.css`, `app.js`, `manifest.webmanifest`, `sw.js`, and icon files
      (SVG is acceptable for the manifest; declare `purpose: "any"`).
      In `create_app` (after routes are registered):
      ```python
      from fastapi.staticfiles import StaticFiles
      mobile_dir = Path(__file__).resolve().parent.parent / "mobile"
      if mobile_dir.is_dir():
          app.mount("/m", StaticFiles(directory=str(mobile_dir), html=True), name="mobile")
      ```
      The auth middleware must let `/m` through like `/docs`: add
      `or path.startswith("/m")` to the open-path branch (~line 285). The
      PAGES are public; the API calls from JS carry the token. Note the
      middleware still rate-limits open paths by host — that stays.~~
      (Done with one change: serves `mobile/pwa/` only, because `mobile/`
      holds the sibling Expo project — its source must not be web content.)
- [x] ~~2.2 **Login screen.** `localStorage` keys: `vave_token`, `vave_host`
      (e.g. `http://192.168.1.20:8765`). Fields: host, token; plus a "pair a
      new device" flow (host + pairing code -> `POST /api/pair`, store the
      returned token). Validate on save with `GET /api/status`. Show the
      device name and a logout button (clears storage only — revocation is
      `DELETE /api/devices/{id}/token` from the desktop GUI).~~
- [x] ~~2.3 **Tasks screen.** Text input + submit -> `POST /api/tasks`
      `{goal, autoplan: true, run: true}`. List recent tasks
      (`GET /api/tasks?limit=20`). Tapping one shows steps with status
      (pending/running/done/failed) and progress. Live updates: one
      EventSource to `{host}/api/events/stream?token={token}`; refresh the
      open task on relevant events; fall back to 5s polling if SSE errors.~~
      (SSE live + 15s poll safety net; task cards always show steps/progress.)
- [x] ~~2.4 **Approvals.** Poll `GET /api/approvals` every few seconds while
      the app is open (or react to SSE events if they carry approval ids —
      check `assistant/events.py` for the event types). Approve/Deny buttons
      -> `POST /api/approvals/{id}` `{"approved": bool}`. Destructive-looking
      approvals should require a second tap (confirm step) in the UI.~~
      (Approve requires a second tap; refresh-driven, no polling loop — SSE + 15s net.)
- [x] ~~2.5 **Notifications.** List from `GET /api/notifications`, unread
      badge, mark-one and mark-all read. (Mark-all: loop the endpoint.)~~
      (Badge + per-item read state; mark-all loops the read endpoint.)
- [x] ~~2.6 **Installable PWA.** `manifest.webmanifest` (name, theme color
      matching the dashboard, display `standalone`, icons). `sw.js` caches
      ONLY the static shell (html/css/js/icons) on install; NEVER cache
      `/api/*` responses. Register with a network-first strategy for the
      shell so updates land.~~
- [x] ~~2.7 **(Optional) QR pairing.** Only if it stays dependency-free or the
      user approves adding `qrcode`. Otherwise the phone types host+code once.~~
      (Deferred to Stretch item "QR pairing for the PWA".)
- [x] ~~2.8 **Docs.** `docs/mobile.md`: pairing, install, what works over LAN,
      security notes (LAN only, tokens are bearer secrets, log out clears
      storage). Add `mobile/` to the repository map in `AGENTS.md` and a
      short subsection in `README.md`.~~

### Automated tests (new file `tests/test_mobile_pwa.py`)
- Static serving: TestClient on `create_app()` -> `GET /m/` returns 200 and
  contains the app title; `GET /m/manifest.webmanifest` returns 200 with the
  right content type; `GET /m/sw.js` returns 200.
- Auth boundary: `GET /api/tasks` WITHOUT a token returns 401 while `/m/`
  paths return 200 (pair a device in the test like
  `tests/test_api_hardening.py` does — reuse that pattern).
- Manifest sanity: JSON parses, has `name`, `icons`, `display` (fetch the file
  from disk, not through HTTP, if simpler).

### Definition of done
- All tasks ticked; suite green locally; clean-install dry run green if any
  dependency or app.py structural change was made; CI green on GitHub.
- Commit suggestion: `feat(mobile): serve installable PWA control client at /m`.

### User manual test (Sohail)
1. `venv\Scripts\python.exe -m assistant.api --host 0.0.0.0 --port 8765`
2. Note the PC's LAN IP; on the phone (same Wi-Fi) open `http://<ip>:8765/m`.
3. On the PC run `venv\Scripts\python.exe -m assistant.api --pair --port 8765`.
4. Enter host + code in the phone UI; confirm it logs in and shows status.
5. Submit "what time is it" (autoplan) and watch the steps complete.
6. Submit something destructive (e.g. "shut down the laptop") and DENY the
   approval from the phone; then approve a harmless one.
7. Check notifications appear and can be marked read.
8. Browser menu -> "Add to Home Screen"; launch standalone; log out and in.
9. Report anything that looks broken or ugly — UI polish is in scope.

---

## 5. Phase 3 — Reliability & Ops

**Goal**: health that sweeps itself, a secrets backup that survives a lost
`data/secret.key`, and an audit view a human can read in the GUI.

### Read first
- `assistant/control/service.py` — find the on-read sweep logic for agents and
  devices (search `sweep` / `offline`); `agent_health()`, `device_health()`-style
  methods; `heartbeat()`.
- `assistant/api/app.py` `lifespan` (auto-resume thread already lives there —
  copy its daemon-thread pattern).
- `assistant/bootstrap.py`, `main.py`, `gui/app.py` — the three entry points.
- `assistant/control/secrets.py` — `SecretStore`, key handling, `put()`/`resolve()`.
- `assistant/task_journal.py`, `assistant/audit.py` — journal + ledger format.
- `gui/pages/activity_page.py` — existing page pattern for the audit view.

### Tasks

- [ ] 3.1 **Background health sweep.** New module `assistant/health_sweep.py`:
      `start(interval_seconds=60)` — idempotent (module-level `_thread` guard),
      daemon thread, loop = sleep interval, then try: get control plane
      (only if already created or creatable cheaply — read `service.py`'s
      `get_control_plane` and decide; if creating the plane at sweep time is
      too heavy, guard with a "plane exists" check and skip otherwise) ->
      call the same sweep functions the read paths use -> swallow+log all
      exceptions (the sweep must never kill anything). Wire it:
      API server: inside `lifespan` next to auto-resume. CLI/GUI: at the end
      of `bootstrap_safety()` in `assistant/bootstrap.py`, behind
      try/except so a sweep failure never blocks startup. Add config
      `health_sweep_seconds` (default 60, 0 disables) to `DEFAULT_CONFIG`
      and `config.json`.
- [ ] 3.2 **Secrets backup runbook + tool.** New module
      `assistant/secrets_backup.py`, runnable as
      `venv\Scripts\python.exe -m assistant.secrets_backup <export|verify|restore> ...`
      - `export --out FILE --passphrase PASS`: bundle `data/secret.key` +
        the encrypted secrets rows (read via a fresh `ControlStore`; serialize
        the raw encrypted values, never decrypted plaintext) as JSON, encrypt
        the whole bundle with a key derived from the passphrase (PBKDF2-HMAC-
        SHA256 or scrypt via `cryptography` — already a dependency), write file.
      - `verify FILE --passphrase PASS`: decrypt, check structure, report
        secret names and capability scopes, confirm key file digest matches
        the bundled one.
      - `restore FILE --passphrase PASS`: requires `--yes` flag; writes
        `secret.key` back and upserts the secret rows into the store. Refuse
        if a `secret.key` already exists unless `--force`.
      - Docs: `docs/secrets-backup.md` with the restore-after-reinstall runbook.
      IMPORTANT: `export` reads real `data/` — that is its job — but tests
      MUST run it with `VAVE_DATA_DIR` pointed at a temp dir.
- [ ] 3.3 **Audit view in GUI.** Extend `assistant/task_journal.py` with
      `recent_entries(limit=50)` parsing only the ACTIVE audit file for
      `task_journal` records (reuse the parsing already in
      `get_weekly_failure_summary`). In the GUI, extend the Activity page
      (or add a modal reachable from it) showing: time, request, outcome
      (color-coded), tools run, step count. Read-only. Follow the EventBus/
      main-thread rules in `AGENTS.md` — build data in a worker thread if
      reading is slow, post through `gui/ui_queue`.

### Automated tests
- `tests/test_health_sweep.py`: `start()` twice -> one thread; with a fake
  plane (mock `assistant.control.service.get_control_plane`) the sweep calls
  the sweep functions; interval 0 disables; exceptions in the plane are
  swallowed (raise inside the mock, sweep survives).
- `tests/test_secrets_backup.py` (uses `VaveTestCase` or explicit
  `VAVE_DATA_DIR` temp): export -> verify -> restore into a SECOND temp dir ->
  `SecretStore` in dir 2 resolves the same secret with the restored key;
  wrong passphrase fails cleanly; restore without `--yes` refuses; existing
  key without `--force` refuses.
- `tests/test_task_journal.py`: extend for `recent_entries` (write N entries
  into a temp ledger, read back, ordering and redaction).

### Definition of done
Suite green, CI green, docs written.

### User manual test (Sohail)
1. Start the API server, pair the phone, then kill the phone's Wi-Fi — after
   ~5 min the Devices page/API should show it offline WITHOUT anyone
   refreshing (that is the sweep working; devices use a 5-min window).
2. `python -m assistant.secrets_backup export --out backup.vault --passphrase ...`
   then `verify`. (Later, on a scratch copy of the data dir, try `restore`.)
3. Open the GUI Activity page: run a few voice/GUI commands, see the journal
   entries appear with outcomes.

---

## 6. Phase 4 — Home Assistant Bridge

**Goal**: "turn off the bedroom light" / "what's the living room temperature"
as first-class VAVE capabilities, over the HA REST API, zero-trust gated.

### Read first
- `assistant/gitlab_agent.py` / `assistant/github_agent.py` — the pattern for
  an API-backed agent module (client class, transport seam for tests,
  module-level tool functions returning strings).
- `assistant/web_api.py` — `web_api_get`/`web_api_call` + credential
  resolution via `secret://`.
- `assistant/control/capabilities.py` — `TOOL_CAPABILITIES` and risk tiers.
- `assistant/guard.py` — `SAFE_TOOLS` / `SENSITIVE_TOOLS` / `REACHES_OUTWARD`.
- `assistant/ai_brain.py` — `AVAILABLE_FUNCTIONS`, `LLM_TOOLS` schema,
  `TOOL_GROUPS`, `SEMANTIC_TOOL_ALIASES`.
- `assistant/api/app.py` — how `/api/google/*` endpoints gate through the
  policy engine (mirror that for `/api/home/*`).

### Tasks

- [ ] 6.1? No — **4.1** `assistant/home.py`: `HomeAssistantClient` with a
      transport seam (`_request(method, path, json)`) like the GitHub agent.
      Config: `home_assistant_url` (default `""` = disabled) added to
      `DEFAULT_CONFIG` + `config.json`. Credential: `secret://homeassistant`
      (a HA long-lived access token), stored with
      `allowed_capabilities="home.*"` — see `SecretStore.put(name, value,
      description="", allowed_capabilities="")` in `secrets.py:93`. When the
      URL or secret is missing, tools return a helpful "not configured" string
      instead of raising.
- [ ] 4.2 **Tools**: `list_home_devices()` (GET `/api/states`, filtered to
      sensible domains: light, switch, climate, media_player, sensor,
      binary_sensor), `get_device_state(entity_id)`, `control_device(entity_id,
      action, value=None)` (on/off/toggle/brightness 0-100/temperature;
      POST `/api/services/<domain>/<service>` with `entity_id`). All return
      readable strings; state changes VERIFY by re-reading the state after
      the call (honest reporting, matching `open_app`'s pattern).
- [ ] 4.3 **Registration**: functions in `AVAILABLE_FUNCTIONS`; schemas in
      `LLM_TOOLS`; new `"home"` entry in `TOOL_GROUPS` (keywords: "light",
      "lights", "lamp", "thermostat", "temperature", "fan", "tv", "home",
      "living room", "bedroom", "kitchen"); aliases in
      `SEMANTIC_TOOL_ALIASES` for "turn on/off". Guard: reads in
      `SAFE_TOOLS`, `control_device` in `SENSITIVE_TOOLS`. Capabilities:
      `home.read` (safe), `home.control` (sensitive) in
      `TOOL_CAPABILITIES`.
- [ ] 4.4 **Voice routing**: NO new anchored regex patterns — the phrases are
      open-ended ("dim the bedroom light to 40") so they must flow to the AI
      brain, which now has the tools. This is deliberate: overlapping
      substring patterns are a known bug source (see AGENTS.md).
- [ ] 4.5 **REST**: `GET /api/home/devices` and `POST /api/home/control`
      following the permission/policy gating used by `/api/google/*`.
- [ ] 4.6 **Docs**: README section + AGENTS.md map entry.

### Automated tests (`tests/test_home_assistant.py`)
Fake transport pattern from `tests/test_developer_git.py`: list devices
parses states; control maps on/off to the right service URL and payload;
brightness/temperature mapping; state verification re-reads after acting;
missing URL/secret -> friendly string; policy: control without permission is
denied (mirror how existing capability tests do it).

### User manual test (Sohail)
1. In HA: create a long-lived access token (Profile -> Security).
2. Store it: start the API, then use the secret store — check
   `docs/control-plane.md`'s secrets section for the exact storage command
   (or ask in the next session; if no CLI exists, a tiny script using
   `plane.secrets.put("homeassistant", token, allowed_capabilities="home.*")`
   is acceptable, kept out of the repo or gitignored).
3. Set `home_assistant_url` in `config.json` (e.g. `http://homeassistant.local:8123`).
4. Say: "what devices are connected to my home", "turn off the bedroom
   light", "set the living room thermostat to 24". Watch the GUI approval
   card for the sensitive control action.

---

## 7. Phase 5 — Packaging & Distribution

**Goal**: `pip install .` works; `vave` command exists; versioned releases.

### Tasks
- [ ] 7.1 `pyproject.toml`: metadata, `version` (start `1.3.0`), pinned deps
      mirrored from `requirements.txt` (keep requirements.txt as the dev
      pin source; pyproject can reference it or duplicate — decide and
      document which is canonical: requirements.txt stays canonical, pyproject
      uses `dependencies` parsed to match).
- [ ] 7.2 Console script `vave = main:main` (check `main.py`'s `main(argv)`
      signature works as an entry point; adjust if it needs `sys.argv`
      handling).
- [ ] 7.3 Subcommands: `vave gui`, `vave serve`, `vave once`, `vave smoke`,
      `vave pair` mapping to existing flags. Implement in `main.py` as an
      argv pre-parser (PowerShell-friendly), keeping ALL current flags
      working (backwards compatible).
- [ ] 7.4 Clean-room test: fresh venv, `pip install .`, run `vave smoke`,
      verify `vave gui` imports (don't open the window in automation).
- [ ] 7.5 README: replace setup section with both flows (git-clone venv flow
      stays; add pip flow). Optional: `scripts/build_exe.ps1` PyInstaller
      spec documented but NOT in CI.

### User manual test (Sohail)
`pip install .` in a fresh venv on your machine, run `vave smoke`, then
`vave gui` and use it for a day.

---

## 8. Stretch (unscheduled — do not start without asking)

- ntfy.sh push bridge for notifications with no connected client.
- Coverage measurement in CI (`coverage run -m unittest`).
- Per-agent credentials (agents currently ride a paired device's token).
- Complete the anchored-regex router for remaining substring commands.
- PostgreSQL store variant (only `store.py` would change, by design).
- QR pairing for the PWA.

---

## 9. Completed (compact record)

- **Phases 1-3 (original stages), Phase 4 control plane, Stage 4.1 waves 0-4,
  Stage 4.5 waves 5-9, Phase 5 Google Workspace + R1-R4, Stage 6A/6B, Stage
  8/9/10/11**: see `context.md` section 3 for the full history.
- **Plan Phase 0** (`f06bc0d`): `disk_warning_gb` setting; TokenBucket NTP
  clock clamp; stale `rate_limit_buckets` pruning; docs drift fixes
  (AGENTS.md, control-plane.md); GitHub Actions CI.
- **Plan Phase 1** (`18e5a21`): migration 0019 + shortcut CRUD; voice
  shortcuts ("when I say X do Y" / delete / list) with reserved-trigger and
  chain guards; `GET /api/commands/frequent` + shortcut REST; GUI suggestion
  chips from real usage; `icons.reset_cache()` on window destroy;
  `playwright>=1.42.0` (greenlet cp313 fix).

## 10. Progress Log

| Date | Entry |
| --- | --- |
| 2026-09-15 | Plan created. Phase 0 started. |
| 2026-09-15 | Phase 0 complete (`f06bc0d`): disk warning, clock clamp, pruning, docs, CI. 704 green. |
| 2026-09-15 | CI dry run caught playwright/greenlet pin + icon-cache bug. Fixed. |
| 2026-09-15 | Phase 1 complete (`18e5a21`): shortcuts end-to-end. 714 green dev + clean venv. |
| 2026-09-15 | Phase 2 complete: PWA in `mobile/pwa/` served at `/m` (login/pair, tasks+SSE, approvals with double-tap, notifications, installable shell, `docs/mobile.md`). Coexists with the sibling Expo app — `/m` serves only `pwa/`. |
