# VAVE Upgrade Plan — Agent Handoff Edition

This file is the single source of truth for the upgrade work. An agent who has
never seen the conversation that produced it must be able to pick it up and
execute a phase alone. Read sections 1-3 fully before touching code.

---

## 1. Current State

- Baseline commit: `18e5a21` on branch `sohail`, remote `origin`
  (`https://github.com/sohail-bhai/VAVE-main.git`).
- Plan Phases 0-5 are COMPLETE (see section 9). Test count: **756**, all
  green locally and on GitHub CI.
- CI: `.github/workflows/ci.yml` (windows-latest, Python 3.13) runs compileall,
  unittest, smoke test on every push. Always check the run after pushing
  (section 3.6).
- `context.md` holds the stage-by-stage history; `AGENTS.md` holds repo rules.
  This file holds the forward plan.
- Phases remaining, in order: **6 (Daily Reliability) -> 7 (Service
  Integrations) -> 8 (Control Plane Enhancements)**. Stretch items are
  unscheduled.

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

- [x] ~~3.1 **Background health sweep.** New module `assistant/health_sweep.py`:
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
      and `config.json`.~~
      (Deviation: wired once in `bootstrap_safety()`, which all three entry
      points share — including the API server. `start()` is idempotent. The
      sweep only touches `service._control_plane` when it already exists, so
      short-lived processes and tests pay nothing.)
- [x] ~~3.2 **Secrets backup runbook + tool.** New module
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
      MUST run it with `VAVE_DATA_DIR` pointed at a temp dir.~~
      (Passphrase key derivation reuses `secrets._coerce_key`; rows carry
      allowed_capabilities so scopes survive the round trip.)
- [x] ~~3.3 **Audit view in GUI.** Extend `assistant/task_journal.py` with
      `recent_entries(limit=50)` parsing only the ACTIVE audit file for
      `task_journal` records (reuse the parsing already in
      `get_weekly_failure_summary`). In the GUI, extend the Activity page
      (or add a modal reachable from it) showing: time, request, outcome
      (color-coded), tools run, step count. Read-only. Follow the EventBus/
      main-thread rules in `AGENTS.md` — build data in a worker thread if
      reading is slow, post through `gui/ui_queue`.~~
      (A "Journal" filter on the Activity page; entries carry an outcome
      tone on the category badge. Tail-read of the live file only.)

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

- [x] ~~4.1 `assistant/home.py`: `HomeAssistantClient` with a
      transport seam (`_request(method, path, json)`) like the GitHub agent.
      Config: `home_assistant_url` (default `""` = disabled) added to
      `DEFAULT_CONFIG` + `config.json`. Credential: `secret://homeassistant`
      (a HA long-lived access token), stored with
      `allowed_capabilities="home.*"` — see `SecretStore.put(name, value,
      description="", allowed_capabilities="")` in `secrets.py:93`. When the
      URL or secret is missing, tools return a helpful "not configured" string
      instead of raising.~~
      (Module functions take `_client_override` like the GitHub agent; narrow
      secret scopes produce a "not allowed" sentence instead of raising.)
- [x] ~~4.2 **Tools**: `list_home_devices()` (GET `/api/states`, filtered to
      sensible domains: light, switch, climate, media_player, sensor,
      binary_sensor), `get_device_state(entity_id)`, `control_device(entity_id,
      action, value=None)` (on/off/toggle/brightness 0-100/temperature;
      POST `/api/services/<domain>/<service>` with `entity_id`). All return
      readable strings; state changes VERIFY by re-reading the state after
      the call (honest reporting, matching `open_app`'s pattern).~~
- [x] ~~4.3 **Registration**: functions in `AVAILABLE_FUNCTIONS`; schemas in
      `LLM_TOOLS`; new `"home"` entry in `TOOL_GROUPS` (keywords: "light",
      "lights", "lamp", "thermostat", "temperature", "fan", "tv", "home",
      "living room", "bedroom", "kitchen"); aliases in
      `SEMANTIC_TOOL_ALIASES` for "turn on/off". Guard: reads in
      `SAFE_TOOLS`, `control_device` in `SENSITIVE_TOOLS`. Capabilities:
      `home.read` (safe), `home.control` (sensitive) in
      `TOOL_CAPABILITIES`.~~
      (Reads registered MEDIUM risk via `home.read`; `control_device` is also
      in `REACHES_OUTWARD` so tainted contexts must confirm.)
- [x] ~~4.4 **Voice routing**: NO new anchored regex patterns — the phrases are
      open-ended ("dim the bedroom light to 40") so they must flow to the AI
      brain, which now has the tools. This is deliberate: overlapping
      substring patterns are a known bug source (see AGENTS.md).~~
- [x] ~~4.5 **REST**: `GET /api/home/devices` and `POST /api/home/control`
      following the permission/policy gating used by `/api/google/*`.~~
      (Control reuses `_held_for_approval`: 202 + approval first, one-shot
      grant spent on the second identical call.)
- [x] ~~4.6 **Docs**: README section + AGENTS.md map entry.~~

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
- [x] ~~7.1 `pyproject.toml`: metadata, `version` (start `1.3.0`), pinned deps
      mirrored from `requirements.txt` (keep requirements.txt as the dev
      pin source; pyproject can reference it or duplicate — decide and
      document which is canonical: requirements.txt stays canonical, pyproject
      uses `dependencies` parsed to match).~~
      (`requirements.txt` stays canonical; pyproject mirrors it with the same
      win32 markers.)
- [x] ~~7.2 Console script `vave = main:main` (check `main.py`'s `main(argv)`
      signature works as an entry point; adjust if it needs `sys.argv`
      handling).~~
      (Installed and verified; `main(argv=None)` reads sys.argv itself.)
- [x] ~~7.3 Subcommands: `vave gui`, `vave serve`, `vave once`, `vave smoke`,
      `vave pair` mapping to existing flags. Implement in `main.py` as an
      argv pre-parser (PowerShell-friendly), keeping ALL current flags
      working (backwards compatible).~~
      (Pure `_expand_subcommand` + `_pair_args`; 9 unit tests.)
- [x] ~~7.4 Clean-room test: fresh venv, `pip install .`, run `vave smoke`,
      verify `vave gui` imports (don't open the window in automation).~~
      (Done: `vave` 1.3.0 installed clean, `vave smoke` 11/11 from a foreign
      cwd. Installed copies read config next to the package — repo checkout
      stays the day-to-day home; README says so.)
- [x] ~~7.5 README: replace setup section with both flows (git-clone venv flow
      stays; add pip flow). Optional: `scripts/build_exe.ps1` PyInstaller
      spec documented but NOT in CI.~~
      (Both flows documented; clone URL fixed to VAVE-main. PyInstaller
      skipped deliberately: no PyInstaller in the tree and the
      model/browser/OCR stack does not freeze cleanly.)

### User manual test (Sohail)
`pip install .` in a fresh venv on your machine, run `vave smoke`, then
`vave gui` and use it for a day.

---

## 8. Phase 6 — Daily Reliability

**Goal**: VAVE's most-used actions (open app, window management, command
routing) stop failing silently and stop misrouting. This is the boring work
that makes the assistant feel solid rather than demo-ware.

### Read first
- `assistant/system_tasks.py` — `open_app` (lines 243-443), `close_app`,
  `activate_window`, `snap_window`, `close_window`, `focus_window`,
  `list_windows`, `_find_window`. The 3-tier app launcher, the window
  management functions, and how they verify (or don't).
- `assistant/commands.py` — `execute_single_command` (the full router),
  `_ATOMIC_INTENTS`, `_QUIT_PATTERN`, `_TIME_PATTERN`, `_LOCK_PATTERN`,
  `_SYSTEM_SHUTDOWN_PATTERN`. How overlapping substrings can misroute.
- `assistant/controller.py` — `process_command` and how it calls
  `execute_command`. What happens on error.
- `assistant/ai_brain.py` lines 79-198 — `AVAILABLE_FUNCTIONS` to see what
  is already registered vs what the command layer can reach.
- `tests/test_smoke.py` and `tests/test_system_tasks.py` — existing test
  patterns for system actions.

### Tasks

#### 6.1 App launch: honest failure reporting
`open_app` Tier 1 uses `os.system("start chrome")` which is fire-and-forget.
The verification loop after it can find the window, but if `os.system` itself
fails (app not installed, broken shortcut), the function returns
"Action sent; effect unconfirmed" — a lie.

- Replace `os.system(cmd)` in Tier 1 with `subprocess.Popen` + return code
  check. `start` is a shell built-in, so use
  `subprocess.run(["cmd", "/c", cmd], capture_output=True)` instead.
- If the subprocess returns a non-zero exit code AND the verification loop
  finds no window, return a clear failure: "Could not open {name}. The
  command exited with code {rc}."
- If the subprocess succeeds but no window appears (UWP apps, background
  services), keep the current "effect unconfirmed" message — that is honest.
- In `close_app`: the `proc.terminate()` call can raise `AccessDenied` for
  admin-owned processes. Catch it and report "Cannot close {name}: access
  denied (try running as administrator)" instead of silently continuing.
- Tests: mock `subprocess.run` to return rc=1 for a missing app, assert
  the failure message. Mock it returning rc=0 with no window found, assert
  "effect unconfirmed". Mock rc=0 + window found, assert success.

#### 6.2 Window management: expose `list_windows` to voice
`list_windows` exists in `AVAILABLE_FUNCTIONS` but has no command-layer
entry point. Users cannot discover what windows are open.

- Add a `_LIST_WINDOWS_PATTERN` regex to `commands.py` matching
  "list windows", "what windows are open", "show open windows",
  "what apps are running".
- Route it in `execute_single_command` (before the AI brain fallback) to
  `system_tasks.list_windows()`. Speak the result.
- Register the pattern in `_shortcut_trigger_reserved` so shortcuts cannot
  shadow it.
- Tests: mock `list_windows` to return a known string, assert the command
  routes to it.

#### 6.3 Command routing: prevent substring misfires
The current router uses substring matching for some commands. Examples of
known false positives:
- "what time is it" matches `_TIME_PATTERN` correctly, but "shutdown time"
  also contains "time" — currently safe because patterns are anchored, but
  "time" as a bare word matches `_TIME_PATTERN` via the `time` alternative.
- "open notes" matches `handle_app_command` (which routes to Tier 3 browser)
  instead of `_ADD_NOTE_PATTERN` (which is checked later).
- "volume" in a sentence like "what's the volume of that container" would
  match `handle_volume_command`.

Fixes:
- Tighten `_TIME_PATTERN`: remove the bare `time` alternative. The user
  never says just "time" — they say "what time is it" or "tell me the time".
- Tighten `_LOCK_PATTERN`: remove bare `lock` — require the object
  ("laptop", "computer", "pc", "screen").
- Move `_ADD_NOTE_PATTERN` and `_READ_NOTES_PATTERN` checks BEFORE
  `handle_app_command` in the router so "open notes" goes to the note
  handler, not the browser.
- Add a compound-command guard in `handle_app_command`: if the command
  contains "note" or "notes", return False early.
- Tests: write a test suite (`tests/test_command_routing.py`) that asserts
  each pattern against known true-positives and known false-positives.
  Include: "what time is it" -> time, "shutdown time" -> AI brain,
  "open notes" -> add_note, "open chrome" -> open_app, "volume of water"
  -> AI brain, "volume up" -> volume, "lock the laptop" -> lock,
  "lock it" -> AI brain.

#### 6.4 Error feedback: speak failures, not silence
When `process_command` catches an exception, it speaks "Something went
wrong" but does not include the command name. When `open_app` or
`close_app` fail, the error is logged but not always spoken.

- In `controller.py` `process_command`: include the command (truncated to
  50 chars) in the error spoken to the user: "Something went wrong with
  '{command snippet}'."
- In `open_app`: the `except` block at line 440 should log the traceback
  at DEBUG (not INFO) and speak the error reason, not just "Could not
  open."
- Tests: no new tests needed — this is a logging/speech change covered by
  existing error-path tests.

### Automated tests
- `tests/test_command_routing.py` (new file): pattern-vs-utterance matrix.
  Each test feeds a command string through `execute_single_command` (with
  mocks for all side effects) and asserts which handler was called.
- Extend `tests/test_system_tasks.py` for 6.1: mock `subprocess.run`
  return codes in `open_app`.

### Definition of done
Suite green, CI green, no new warnings from compileall.

### User manual test (Sohail)
1. Say "open chrome" — should open or report failure honestly.
2. Say "open notepad" — should open and focus.
3. Say "list windows" — should enumerate open windows.
4. Say "open notes" — should go to the note handler, not the browser.
5. Say "lock the laptop" — should lock.
6. Say "lock it" — should NOT lock (goes to AI brain).
7. Say "what time is it" — should answer.
8. Say "shutdown time" — should NOT answer with the time (goes to AI brain).

---

## 9. Phase 7 — Service Integrations

**Goal**: Google Calendar, email, and Telegram — the three services you
actually use daily — work end-to-end through VAVE without touching the GUI.

### Read first
- `assistant/calendar_sync.py` — `get_calendar_service` (OAuth flow),
  `get_upcoming_events`, `schedule_event`. Uses `credentials.json` +
  `token.json` at project root. The Google API client is already a
  dependency.
- `assistant/email_tasks.py` — `read_unread_emails` (IMAP), `send_email`
  (SMTP + Workspace fallback), `draft_email` (Workspace). Credentials come
  from `config.json` (`email_address`, `email_app_password`) or the secret
  store.
- `assistant/telegram_sync.py` — `start_telegram_sync`, `send_telegram_message`,
  `get_active_telegram_pairing_code`. Already partially wired in the
  controller.
- `assistant/ai_brain.py` lines 79-198 — `AVAILABLE_FUNCTIONS` (calendar
  and email tools are already registered: `get_schedule`, `schedule_meeting`,
  `read_unread_emails`, `send_email`).
- `assistant/commands.py` — `_CALENDAR_READ_PATTERN`, `_EMAIL_SEND_PATTERN`.
  Calendar reads route to `get_upcoming_events`; email send routes to AI.
- `assistant/control/capabilities.py` — `google.calendar.*`, `google.gmail.*`
  are already in the catalog.

### Tasks

#### 7.1 Calendar: voice commands that work without the AI brain
Currently "what's my schedule" matches `_CALENDAR_READ_PATTERN` and calls
`get_upcoming_events()` directly — that works. But "schedule a meeting
tomorrow at 3pm with Alex" goes to the AI brain, which has to parse the
natural language, call `schedule_meeting`, and hope the ISO timestamp is
right.

- Add `_CALENDAR_TOMORROW_PATTERN` matching "schedule a meeting/call/event
  tomorrow at <time>" — extract the time, build ISO format with tomorrow's
  date, call `schedule_event` directly.
- Add `_CALENDAR_CANCEL_PATTERN` matching "cancel my meeting/call/event
  tomorrow" or "cancel the <name> meeting" — call a new
  `cancel_event(summary=None, event_id=None)` function.
- Implement `cancel_event` in `calendar_sync.py`: list events for today+
  tomorrow, find by summary or ID, call `service.events().delete()`.
  Return the cancelled event's summary for spoken confirmation.
- Register `cancel_event` in `AVAILABLE_FUNCTIONS` and `TOOL_CAPABILITIES`
  (`google.calendar.write`).
- Tests: mock `get_calendar_service` to return a fake service, assert
  `schedule_event` builds the right event body, assert `cancel_event`
  calls delete with the right event ID.

#### 7.2 Email: read and send without the AI brain
"read my emails" already works via `read_unread_emails`. But "send an email
to Alex saying ..." goes through the AI brain. Make common email actions
fast-path:

- Add `_EMAIL_READ_PATTERN` matching "read my emails", "check my inbox",
  "any new emails" — route to `read_unread_emails()` directly (currently
  falls through to AI).
- Add `_EMAIL_REPLY_PATTERN` matching "reply to the last email saying <text>"
  — extract the body, look up the last email's sender from the inbox, call
  `send_email(to, subject, body)`.
- Implement `get_last_email_sender()` in `email_tasks.py`: read the last
  unread email's From header, return the address.
- Register `get_last_email_sender` in `AVAILABLE_FUNCTIONS` (if needed for
  the AI brain to use).
- Tests: mock `imaplib` for `read_unread_emails`, assert the pattern routes
  correctly. Mock the IMAP fetch for `get_last_email_sender`.

#### 7.3 Telegram: verify the sync loop works end-to-end
The Telegram bridge exists but may not be actively running. Verify and fix:

- Check `telegram_sync.py` `start_telegram_sync`: does it start a polling
  thread? Does it handle the bot token from config?
- If the bot token is missing or invalid, `start_telegram_sync` should log
  a clear warning at startup (not crash silently).
- Add a voice command "send a telegram to myself saying <text>" that calls
  `send_telegram_update` directly (currently this is in `AVAILABLE_FUNCTIONS`
  but has no command-layer entry).
- Add `_TELEGRAM_SEND_PATTERN` matching "send a telegram/message to myself
  saying <text>" — route to `system_tasks.send_telegram_update(text)`.
- Tests: mock `telegram_sync.send_telegram_message`, assert the pattern
  routes and calls it with the right text.

#### 7.4 Secret store: credentials via `secret://` instead of config.json
Email credentials (`email_address`, `email_app_password`) are currently
read from `config.json` in plaintext. The secret store already supports
this but the code path is not used.

- Add a helper `resolve_credential(key, capability)` in
  `assistant/control/secrets.py` that checks `config.json` first, then
  falls back to `secret://<key>` resolution.
- Update `email_tasks.py` to use `resolve_credential` instead of raw
  `get_setting` for `email_app_password`.
- Update `calendar_sync.py` to store the OAuth token in the secret store
  after first auth (optional — the current `token.json` approach works,
  but storing in the secret store means the token survives repo copies).
- Docs: add a short section to `docs/control-plane.md` showing how to
  store email credentials: `plane.secrets.put("email_app_password", "...",
  allowed_capabilities="google.gmail.*")`.
- Tests: mock `SecretStore.put` and `SecretStore.resolve`, assert the
  helper round-trips correctly.

### Automated tests
- `tests/test_calendar_integration.py` (new file): fake Google Calendar
  service, test `get_upcoming_events`, `schedule_event`, `cancel_event`.
- `tests/test_email_integration.py` (new file): mock IMAP/SMTP, test
  `read_unread_emails`, `get_last_email_sender`, pattern routing.
- `tests/test_telegram_integration.py` (new file): mock
  `send_telegram_message`, test pattern routing.

### Definition of done
Suite green, CI green. Voice commands "what's my schedule", "read my emails",
"send a telegram to myself saying hello" all work via fast-path routing.

### User manual test (Sohail)
1. Set up Google Calendar OAuth: place `credentials.json` in the project root,
   say "what's my schedule" — should list upcoming events.
2. Say "schedule a meeting tomorrow at 3pm with Alex" — should create the
   event and confirm.
3. Say "read my emails" — should list unread emails.
4. Say "send a telegram to myself saying test from VAVE" — should deliver
   via the Telegram bot.
5. Store email credentials in the secret store, verify the `secret://`
   path works.

---

## 10. Phase 8 — Control Plane Enhancements

**Goal**: VAVE becomes proactive rather than purely reactive — it discovers
devices on the network, suggests actions based on context, and uses the
wake word engine as a first-class input path.

### Read first
- `assistant/wakeword.py` — `_load_wakeword_model`, `_wakeword_worker`,
  `start_wakeword_engine`, `pause_wakeword`, `resume_wakeword`. The engine
  runs in a daemon thread, detects a wake word, calls a callback, then
  pauses itself. Currently wired in `controller.py` `_on_wakeword_detected`.
- `assistant/controller.py` lines 99-117 — `_on_wakeword_detected`: pauses
  wake word, calls `run_once()`, resumes wake word. This is correct but
  the callback could be improved.
- `assistant/control/service.py` — `ControlPlane`, `get_control_plane`,
  `subscribe`. How events flow.
- `assistant/control/executor.py` — `TaskExecutor`, `resume_interrupted`.
  How tasks run.
- `assistant/config.py` — `DEFAULT_CONFIG`, `get_setting`. Where new config
  keys go.
- `assistant/health_sweep.py` — the daemon sweep thread pattern.
- `assistant/memory.py` — `remember_fact`, ChromaDB vector memory.
- `assistant/ai_brain.py` — `query_local_llm_chat`, the Ollama-backed
  model. How to add proactive suggestion generation.

### Tasks

#### 8.1 Network device discovery
VAVE should detect devices on the local network so phone pairing is
easier and the "what's connected to my home" question has a local answer
independent of Home Assistant.

- New module `assistant/discovery.py`:
  - `discover_devices(timeout=5)` — on Windows, parse `arp -a` output to
    find IP + MAC + hostname. On Linux, use `ip neigh`. On macOS, use
    `arp -a`. Return a list of dicts: `{ip, mac, hostname, vendor}`.
  - `get_local_device()` — return this machine's hostname and LAN IP
    (from `socket.gethostname()` + `socket.gethostbyname()`).
  - Cache results for 60 seconds to avoid repeated scans.
  - The function is non-blocking: if the scan takes longer than `timeout`,
    return whatever was found.
- Register in `AVAILABLE_FUNCTIONS`: `discover_network_devices` (no
  capability needed — LAN-only, non-sensitive).
- Add to `TOOL_GROUPS`: "network" group with keywords "devices", "network",
  "connected", "who is on my network".
- Wire a voice command: "what devices are on my network" routes through the
  AI brain (open-ended, not a fixed-schema query).
- Tests: mock `subprocess.check_output` for `arp -a`, assert the parser
  returns the right device list. Test cache: second call within 60s returns
  cached result.

#### 8.2 Proactive suggestions engine
VAVE should suggest actions based on time, calendar, and context — not
wait to be asked.

- New module `assistant/proactive.py`:
  - `check_suggestions()` — returns a list of suggestion strings based on:
    - Time of day: morning (briefing), lunch (lunch reminder), evening
      (wind-down).
    - Calendar: meeting in 10 minutes -> "You have {event} in 10 minutes.
      Want me to prepare?"
    - Battery: below 20% -> "Battery is at {n}%. Want me to plug in?"
    - Health sweep: agent offline -> "Agent {name} went offline."
  - Each suggestion has a cooldown: the same suggestion is not repeated
    within 30 minutes.
  - `start_proactive_engine(interval_seconds=300)` — daemon thread, like
    `health_sweep.py`. Checks every 5 minutes, emits suggestions through
    the event bus as `EVENT_STATUS` events. The GUI can show them as
    toasts; the CLI logs them; the API can expose them.
  - Config: `proactive_enabled` (default True), `proactive_interval_seconds`
    (default 300).
- Wire in `bootstrap_safety()` next to the health sweep start.
- Register `get_proactive_suggestions` in `AVAILABLE_FUNCTIONS` so the
  AI brain can be asked "what should I do?" and get the list.
- Tests: mock `get_setting` for time-of-day, mock `get_upcoming_events`
  for calendar, assert suggestions are generated. Test cooldown: same
  suggestion not repeated within the window.

#### 8.3 Wake word: continuous hands-free mode
The wake word engine already works but is toggled by a config flag and
only triggers a single `run_once()`. Make it a first-class mode:

- Add `wake_word_mode` config with values: "off" (default for now),
  "continuous" (always listening), "push-to-talk" (current behavior:
  wake word triggers one listen, then pauses).
- In continuous mode: after `run_once()` completes, do NOT pause the wake
  word engine — let it keep listening. The current code in
  `_on_wakeword_detected` calls `pause_wakeword()` then `resume_wakeword()`
  after `run_once()` — in continuous mode, skip the pause.
- Add a voice command "enable hands-free" / "disable hands-free" that
  toggles `wake_word_mode` between "continuous" and "off".
- In the GUI: show the wake word state (listening/idle) in the status bar.
  Emit an event `EVENT_WAKE_WORD` when the wake word is detected so the GUI
  can flash a visual indicator.
- Tests: mock `_on_wakeword_detected`, assert that in continuous mode the
  engine is not paused. Assert that in push-to-talk mode it is paused.

#### 8.4 Event bus: proactive notifications to phone
The `Notifier` already pushes events to Telegram. Extend it to push
proactive suggestions:

- When `check_suggestions()` generates a suggestion, emit it as a
  `EventType.TASK_COMPLETED` event (or a new `EventType.SUGGESTION` if
  one is added) so the notifier picks it up and sends it to the phone.
- This means "meeting in 10 minutes" appears on the phone without the
  user asking — the PWA picks it up via SSE.
- Tests: subscribe to the event bus, run `check_suggestions`, assert the
  event is emitted.

### Automated tests
- `tests/test_discovery.py` (new file): mock `arp -a` output, test
  parsing, test cache, test timeout.
- `tests/test_proactive.py` (new file): mock time/calendar/battery,
  test suggestion generation, test cooldown, test event emission.
- `tests/test_wakeword_mode.py` (new file): test continuous vs
  push-to-talk behavior via mocked wake word engine.

### Definition of done
Suite green, CI green. "What devices are on my network" returns a device
list. "Enable hands-free" activates continuous listening. Suggestions
appear in the GUI status bar and on the phone via SSE.

### User manual test (Sohail)
1. Say "what devices are on my network" — should list devices with IPs.
2. Say "enable hands-free" — the wake word should keep listening after
   each command without pausing.
3. Say "disable hands-free" — should revert to push-to-talk.
4. Wait 5 minutes with a meeting coming up — a suggestion should appear
   in the GUI and/or on the phone.
5. Let the battery drop below 20% — a suggestion should appear.

---

## 11. Stretch (unscheduled — do not start without asking)

- ntfy.sh push bridge for notifications with no connected client.
- Coverage measurement in CI (`coverage run -m unittest`).
- Per-agent credentials (agents currently ride a paired device's token).
- Complete the anchored-regex router for remaining substring commands.
- PostgreSQL store variant (only `store.py` would change, by design).
- QR pairing for the PWA.

---

## 12. Completed (compact record)

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
- **Plan Phase 2** (`92fe7fd`): PWA in `mobile/pwa/` served at `/m`
  (login/pair, tasks+SSE, approvals with double-tap, notifications,
  installable shell-only service worker, `docs/mobile.md`). Serves only
  `pwa/`, Expo project untouched.
- **Plan Phase 3** (`a3042aa`): background health sweep
  (`assistant/health_sweep.py`, wired in `bootstrap_safety()`), secrets
  backup tool + runbook (`docs/secrets-backup.md`), GUI Journal filter on
  the Activity page. 733 green (719 baseline + 14 new).
- **Plan Phase 4** (`3c6deb1`): `assistant/home.py` over the HA REST API
  (states, verified control), `home.read`/`home.control` capabilities,
  brain + guard registration, `GET /api/home/devices` + approval-held
  `POST /api/home/control`. 14 new tests.
- **Plan Phase 5** (`8a28828`): `pyproject.toml` (v1.3.0, `vave` console
  script), subcommands `gui/serve/once/smoke/pair`, clean-room
  `pip install .` + `vave smoke` 11/11 verified, README pip flow. 9 new
  tests. 756 green.

## 13. Progress Log

| Date | Entry |
| --- | --- |
| 2026-09-15 | Plan created. Phase 0 started. |
| 2026-09-15 | Phase 0 complete (`f06bc0d`): disk warning, clock clamp, pruning, docs, CI. 704 green. |
| 2026-09-15 | CI dry run caught playwright/greenlet pin + icon-cache bug. Fixed. |
| 2026-09-15 | Phase 1 complete (`18e5a21`): shortcuts end-to-end. 714 green dev + clean venv. |
| 2026-09-15 | Phase 2 complete: PWA in `mobile/pwa/` served at `/m` (login/pair, tasks+SSE, approvals with double-tap, notifications, installable shell, `docs/mobile.md`). Coexists with the sibling Expo app — `/m` serves only `pwa/`. |
| 2026-09-15 | Phase 3 complete: background health sweep (`assistant/health_sweep.py`, wired in `bootstrap_safety()`), secrets backup tool + runbook (`docs/secrets-backup.md`), GUI Journal filter on the Activity page. 733 green (719 baseline + 14 new). |
| 2026-09-15 | Phase 4 complete: `assistant/home.py` over the HA REST API (states, verified control), `home.read`/`home.control` capabilities, brain + guard registration, `GET /api/home/devices` + approval-held `POST /api/home/control`. 14 new tests. |
| 2026-09-15 | Phase 5 complete: `pyproject.toml` (v1.3.0, `vave` console script), subcommands `gui/serve/once/smoke/pair`, clean-room `pip install .` + `vave smoke` 11/11 verified, README pip flow. 9 new tests. Temp venvs removed (C: critically low at ~5 GB free — user cleanup needed). |
| 2026-09-15 | ALL PHASES 2-5 DONE. 756 tests green locally and on GitHub CI (`success` on `8a28828`). Commits: `92fe7fd` (PWA), `a3042aa` (sweep/backup/journal), `3c6deb1` (Home Assistant), `8a28828` (packaging). |
| 2026-09-16 | Reliability sprint after live-testing: 3-tier model routing, safety guard (direct + hypothetical destructive requests), vision auto-analyze, deterministic step tracking, Store/UWP launcher, Chromium accessibility nudge, console-safe window matching, physical clicks for web surfaces, screenshot-OCR click route (RapidOCR). 768 green. |
| 2026-09-16 | Section 14 added: DeepSeek suggestions for leveling VAVE into a true system (brainstorm only, no code). |
| 2026-09-16 | Section 15 added: live-test defect register with root causes, fixes, commits, and verification steps for the next agent. |

---

## 14. DeepSeek Suggestions — Leveling VAVE Into a True System

> Brainstorm only. No code is written from this section until the user picks an
> item. It exists so the next agent can propose and scope work without
> re-deriving the reasoning.

### 14.0 Guiding principles

1. **The hard part is grounding, not intelligence.** The 2026-09-16 live tests
   proved it: the 3B model picked the right tool and the right action, then
   failed because it could not *see* the page. A bigger model calling the same
   `click_element` on an invisible window fails identically. Every headline
   suggestion below is about giving the model a truthful world to act on.
2. **Actions must be verifiable, or they are guesses.** "Clicked X" is not a
   result; "clicked X and the scene changed / did not change" is.
3. **A control plane is judged by its worst day, not its demo.** Dry runs,
   tamper-evident audit, degraded-mode honesty, interrupt-safe tasks.
4. **Fit the hardware.** RTX 3050 4GB + 16GB RAM is the target. Nothing here
   may assume a frontier model.

---

### TIER S — Highest leverage, builds directly on what just shipped

#### S1. One grounded World Model: `perceive()` instead of tool roulette
**What.** A new `assistant/grounding.py` exposing `perceive(window=None) ->
Scene` and `act(element_id, action)`. `Scene` merges the three senses VAVE
already has but never combines: the UIAutomation tree, screenshot OCR lines
(`vision.ocr_screen`), and an optional VLM caption (`vision.analyze_screen`).
It returns one ranked list of interactive elements, each with a **stable id**
(`w3.e7`), label, kind, bounding box, and source tag (`uia` / `ocr` / `vlm`).

**Why it stands out.** This is the single biggest differentiator. Today
`click_element`, `click_at`, `find_and_click_text`, `read_screen` and
`get_clickable_elements` each guess independently, so the model sees four
inconsistent views of one screen. The "Edge toolbar button named `Profile 1
Profile`" bug is a *class* of failure that only a fused view removes. Stable
ids also kill label collisions: the model acts on `w3.e7`, not on a name that
might match three controls.

**Lands in.** New `assistant/grounding.py`; `system_tasks` click/read tools
become thin wrappers over `perceive`/`act`; `ai_brain` tool schemas collapse
toward `perceive_window`, `act`, `read_scene`.

**Effort/risk.** Large (2-3 sessions). Risk: tree walks and OCR are slow, so
the Scene must cache per window handle + revision, and only re-scan on act.

#### S2. Scene-diff after every action (make "nothing happened" machine-readable)
**What.** Hash the Scene before and after each acting tool. Return a uniform
result envelope: `{ok, changed: bool, delta, detail}`. "The screen is
identical after your click" becomes a first-class signal the loop can react to
instead of the current prose hint (`acted_since_look`).

**Why it stands out.** Loop detection today is text-based inference. A real
diff is exact: it ends the click-eight-times dead-end at the source, and feeds
the retire-the-model-attempt logic in `_agent_loop` a trustworthy input.

**Lands in.** `assistant/grounding.py` + the `performed` bookkeeping in
`ai_brain._agent_loop`.

**Effort/risk.** Medium, and it depends on S1.

#### S3. Dry-run / rehearsal mode
**What.** A global `dry_run` flag (config + CLI + a voice phrase "rehearse
that"). In dry run the planner and agent loop execute normally, but every tool
in `guard`'s `destructive` tier and every tool in `REACHES_OUTWARD` returns a
**simulated** result ("WOULD delete files under C:\\Users\\...", "WOULD send
email to alex@..."), and the run ends with a plan summary.

**Why it stands out.** This is the direct answer to the user's live problem:
"it is still very risky to use it for anything related to deleting files — I
can't even safely test it." Dry run makes the *entire* automation stack
testable against real commands with zero consequence, and it doubles as a
trust feature ("show me what you'd do first").

**Lands in.** `assistant/guard.py` (a `_dry_run` gate before `func(**kwargs)`),
`bootstrap.py` config plumbing, `commands.py` phrase, `ai_brain` summary.

**Effort/risk.** Small-to-medium, high value. Risk: some tools' *returns* are
what later steps depend on, so simulated results must stay structurally real.

#### S4. Episodic tool memory (procedural memory, not just facts)
**What.** When a multi-step goal completes, store the successful tool sequence
keyed by an embedding of the goal ("sign into netflix profile" -> `browse ->
perceive -> act(profile tile) -> ...`). On a similar future goal, retrieve the
top-k traces and inject them as few-shot exemplars / a suggested plan.

**Why it stands out.** `memory.py` today stores *facts*. A 3B model with a
concrete successful exemplar behaves far better than a 4B model starting cold
— and it is the cheapest quality win available on 4GB VRAM. It also makes VAVE
measurably improve with use, which is what "true system" means.

**Lands in.** `assistant/memory.py` (new collection `task_traces`),
`ai_brain._agent_loop` (retrieval + injection), `task_journal` (source of
successful traces).

**Effort/risk.** Medium. Risk: storing traces must reuse `audit.redact` so no
secret or argument leaks into the vector store.

#### S5. `vave doctor`
**What.** One command that checks every subsystem and prints a pass/fail
report with a fix hint per failure: Ollama reachable + which models present,
fast/smart/deep tier readiness, mic + TTS availability, PyAudio, Playwright +
Chromium, Tesseract/RapidOCR, uiautomation, `data/` writability, `secret.key`
present, disk free, API port free, config schema version.

**Why it stands out.** Support and first-run experience are what separate a
project from a product. Today every failure mode ("Ollama is not running",
"model not installed") is discovered mid-task by the user.

**Lands in.** New `assistant/doctor.py`, `main.py` subcommand `vave doctor`.

**Effort/risk.** Small. Pure reads; no side effects.

#### S6. Tamper-evident audit ledger (hash chain)
**What.** Each entry in `logs/audit.jsonl` stores `prev_hash` and
`entry_hash = H(prev_hash || canonical(entry))`. Add `vave audit verify` (or
`python -m assistant.audit verify`) that walks the chain and reports the first
broken link.

**Why it stands out.** VAVE already markets itself as a zero-trust control
plane whose ledger is its memory of what it did. A ledger that cannot prove it
was not edited is the missing half of that claim. Cheap to add, hard to fake.

**Lands in.** `assistant/audit.py` (append path + verifier), `task_journal`
readers that must tolerate legacy unhashed lines.

**Effort/risk.** Small. Risk: must not break existing tail-read parsers
(`recent_entries`, `get_weekly_failure_summary`).

---

### TIER A — Strong differentiators

#### A1. Capability leases (kill approval fatigue)
Per-call approvals are correct but exhausting, and exhaustion trains users to
approve without reading. Add time-boxed / task-scoped grants: "allow
`home.control` for this task" or "for 10 minutes", shown as a visible countdown
in the GUI and on the phone, revoked by the stop switch. `policy.py` already
has the allow/ask/deny shape; this adds a lease table + expiry.

#### A2. Routine mining (observe, then offer)
The control plane records every task in the ledger. Add a periodic pass that
detects repeated manual sequences (open VS Code -> open terminal -> open docs,
most weekday mornings) and *offers* to turn them into a routine or shortcut.
Routines and shortcuts already exist — this is the missing "notice" step that
makes proactivity concrete instead of generic.

#### A3. Global command palette hotkey
A tiny always-on-top intent box on a hotkey (e.g. `Ctrl+Space`), separate from
the heavy GUI. Type or paste a goal; watch steps inline. This is the fastest
human input channel and the one power users actually adopt. Keep it plain
CustomTkinter per the repo rules — **no** web/Electron.

#### A4. `vave trace` + task replay
A human-readable timeline per task: step, tool, redacted args, duration,
outcome, and the Scene-diff verdict from S2. Then `vave replay <task_id>` to
re-run it in dry-run mode. Turns the audit ledger from a compliance artifact
into a debugging tool.

#### A5. Degraded-mode honesty
At startup and on demand, detect which senses/brains are missing and *say so*:
"No local brain — running the fixed command router only." "OCR unavailable —
browsers may be read-only." Never let the user discover a missing subsystem by
watching a task fail three steps in. Pairs with `vave doctor`.

#### A6. Notification actions everywhere (not just approvals)
The phone PWA can already approve/deny. Extend actionable notifications:
**Retry**, **Cancel**, **Open on desktop**, **Snooze** — all over the existing
`/api/notifications` + SSE. A control plane you cannot act on from your pocket
is only half a control plane.

#### A7. Low-confidence destructive-verb confirmation
When ASR confidence is low on a destructive word (delete, format, shut down,
wipe), ask before routing — even if the phrase is otherwise valid. Cheap use of
the confidence data SpeechRecognition already returns; directly reduces the
scariest failure mode.

---

### TIER B — Polish that reads as maturity

- **B1. Model warm-keeping.** A light keep-alive ping on the active tier so the
  first command after idle is not a cold load. Also set Ollama `keep_alive`
  sensibly per tier.
- **B2. Batch read-only tools.** One `perceive`/`read_scene` call instead of
  five sequential inspections — fewer turns, less latency on a small model.
- **B3. Idempotency keys for outward actions.** A retried `send_email` /
  `control_device` must not double-fire. Store a short-lived key per logical
  action in `control/store.py`.
- **B4. Per-tool metrics.** Extend the control plane with a `/api/metrics`
  text endpoint: call counts, error rate, p50/p95 latency per tool. Makes
  "which tool is flaky" a data question.
- **B5. Crash supervisor.** If the API or GUI dies, relaunch and report the
  crash with the last 20 log lines. The system should not just stop.
- **B6. Barge-in first-class.** The `interrupter` exists; make "user started
  talking" always stop TTS mid-sentence, not only on a hotkey.
- **B7. Follow-up listening window.** After a command, keep the mic open a few
  seconds for "and also ...". Natural multi-part requests without re-waking.
- **B8. Brief vs detailed reply mode.** A per-session verbosity setting, so a
  "true system" can be terse on request.

---

### TIER C — Research / only with explicit sign-off

- **C1. Fake-desktop CI harness.** A deterministic fake window tree + fake OCR
  so the whole grounding/click stack is testable in CI without a display. Would
  have caught the Chromium-blindness bug in unit tests.
- **C2. VLM grounding calibration.** Teach `analyze_screen` to return
  normalized coordinates ("the profile tile is at 0.37, 0.44") and calibrate
  against known screens; a fallback sense when UIA and OCR both fail (icons,
  images, canvas).
- **C3. Durable swarm workers.** `swarm.py` sub-agents are in-process; persist
  long-running helpers as control-plane agents that survive restarts.
- **C4. Signed releases + `vave update --check`.** Release signing and an
  in-app update check; PyInstaller remains explicitly out of scope.
- **C5. Prompt-injection hardening at the planner.** Today taint is handled at
  the guard. Also mark untrusted content at the planner so a plan is never
  *derived* from page text, only from the user.

---

### Recommended sequence for the next session

1. **S3 dry-run** — smallest change, directly unblocks safe live testing.
2. **S5 `vave doctor`** — smallest change, biggest first-run/quality-of-life
   win; also tells us the machine's real capability envelope.
3. **S1 `perceive()` + S2 scene-diff** — the foundational grounding work; do it
   as one phase with S1's tests.
4. **S6 audit hash chain** — cheap, and it hardens the artifact everything else
   is judged by.
5. **S4 episodic memory** — after grounding is stable, so traces are worth
   replaying.

### Explicit non-goals (don't drift here)

- No web/Electron/Node UI layers (repo rule); the palette is CustomTkinter.
- No frontier-model or cloud-LLM dependency; the target is 3B-4B local.
- No restructuring of `mobile/` (another developer's tree).
- No weakening of `guard`/`confirm`/`audit`/`overwatch`; every item above must
  route through them, not around them.

---

## 15. Live-Test Defect Register & Handoff (2026-09-16)

> This is the handoff for the next coding agent. Section 14 is *ideas*; this
> section is *what actually broke, why, what was done, and how to prove it*.
> Every entry has a symptom the user saw, the real root cause found by
> instrumenting, the fix commit, and a verification step. Read this before
> touching pointer/vision/launcher code.

### 15.0 Ground truth: what the user experienced

The user ran the desktop GUI and hit, in order: a destructive "what would you
do if I said delete my files" that went hunting through File Explorer; an
"open netflix" that opened the Microsoft Store / a browser instead of the app;
and "open netflix and open sohail profile" that looped forever clicking a
button and never opened the profile. The net finding: **the model was not the
problem — it could not see the screen.** Details below.

### 15.1 Commit map (this session)

| Commit | What it fixes | Defect |
| --- | --- | --- |
| `9d8ee72` | weather / list_windows / notes routing fast-paths | routing |
| `174d2b9` | no tool calls for conversational input; `num_gpu` config | D2 |
| `22d8849` | 3-tier model routing (fast/smart/deep) | D3 |
| `11370fc` | 3-tier edge cases (thresholds, disabled tiers, strikes) | D3 |
| `6f4845b` | block destructive natural-language requests | D4 |
| `81cfff0` | screenshots auto-analyzed by the VLM | D5 |
| `88b4853` | deterministic step tracking (no per-step LLM call) | D1 |
| `4119dd4` | hypothetical destructive phrasing + Netflix browser fallback | D4, D6 |
| `96baa44` | generic browser fallback when native app is missing | D6 |
| `09bcd80` | Store/UWP apps launched via PowerShell | D6 |
| `12e3fa9` | resolve the real Store AppID at runtime | D6 |
| `3a5ff5e` | terminals excluded from window-title matching | D7 |
| `bdd0697` | real mouse click for Chromium; stall nudge every repeat | D8 |
| `5d9ea80` | force Chromium accessibility (`WM_GETOBJECT`) | D8 |
| `e14abd4` | robust nudge: all process HWNDs + foreground | D8 |
| `a1e254a` | screenshot-OCR click route via RapidOCR | D9 |
| `990d71c` | OCR page entries in scans, chrome exclusion from scans, chrome-guard,
  profile trigger, prompt rule, OCR hint in stall redirect, OCR rank 4,
  `take_screenshot(analyze=…)`, both regression files (D4+D8), eager vision
  imports (sys.modules wipe trap) | D8, D4, O4 |

### 15.2 Defects

#### D1 — Multi-step tasks took 40-50s
- **Symptom.** "Open netflix and pick a profile" took ~45s even on the fast
  model.
- **Root cause.** `_remaining_work()` made a *separate* LLM call after every
  step just to ask "is it done?". A 3-step request cost ~7 model calls.
- **Fix.** `88b4853`: deterministic sequential mapping (each non-inspection
  tool call completes one step); the LLM verdict is only used on ambiguity.
- **Verify.** `venv\Scripts\python.exe -m unittest tests.test_chained_tasks`
  (20 tests). Watch a 3-step task in the log: model calls should be ~3, not ~7.

#### D2 — Conversational input triggered tools
- **Symptom.** "hello" / "what is 2+2" caused tool calls (`tell_date`, random
  tools) instead of a plain answer.
- **Root cause.** `select_tools()` offered tools for every input; the model
  then felt obliged to call one.
- **Fix.** `174d2b9`: conversational suppression (greetings, math, jokes,
  knowledge questions get an empty tool list) + a "WHEN NOT TO USE TOOLS"
  block in `get_system_prompt()`.
- **Verify.** `select_tools("hello there")` returns `[]`;
  `select_tools("open notepad")` returns tools.

#### D3 — Single model, GPU crash, no fallback
- **Symptom.** The default model (`qwen3.5:9b`) crashed the 4GB GPU.
- **Root cause.** No tiering; a 9B model cannot run on this laptop.
- **Fix.** `22d8849`, `11370fc`: fast (`qwen2.5:3b`, GPU) / smart (`qwen3:4b`,
  partial GPU) / deep (opt-in, RAM-gated); rule-based classifier; escalation
  with strike tracking; "switch to fast/smart/deep/auto" voice commands.
- **OPEN (see 15.3 O1).** The deep tier (`phi4`) cannot load — RAM, not code.

#### D4 — Destructive natural-language requests were not blocked
- **Symptom.** "delete all my files" (and the hypothetical "what would you do
  if I said delete my files") was attempted; the user saw File Explorer
  hunting through documents and killed the process.
- **Root cause.** `guard._DESTRUCTIVE_COMMAND_REGEX` only matched *shell*
  patterns (`rm -rf`, `del /f`, `diskpart`). Natural language never reached a
  shell string, so nothing matched.
- **Fix.** `6f4845b`: `guard.is_destructive_request(text)` — request-level
  patterns, hooked into `ask_ai()` and `run_task_step()` *before* the model,
  with a spoken refusal and an audit record. Safe overrides keep "delete this
  note", "clear my notes", "shutdown" working. `4119dd4`: hypothetical/indirect
  phrasing ("what would you do if I said…", "can you delete…", "help me wipe…")
  also blocked.
- **Verify.** Ad-hoc matrix (17 + 11 phrases) was run; for the handoff, add a
  permanent `tests/test_destructive_requests.py` asserting the true/false set:
  block "delete all my files" / "wipe my drive" / "what if I asked you to erase
  everything"; allow "delete this note" / "shutdown" / "open notepad".
- **NOTE.** The user's incident happened while testing; after `6f4845b` +
  `4119dd4` the request-level guard rejects it. **Never remove that guard**, and
  keep it *before* the model (a model can always be talked into trying).

#### D5 — Screenshots were saved but the model could not see them
- **Symptom.** `take_screenshot` returned a file path; the model had no image
  input and so learned nothing.
- **Root cause.** The tool returned a string, never an analysis.
- **Fix.** `81cfff0`: `take_screenshot` now runs the local VLM
  (`moondream:latest` via `vision.analyze_screen`) and returns the path **plus**
  a description; a VLM failure falls back to path-only (never breaks capture).
- **Verify.** With Ollama running, `take_screenshot` returns a "Screen
  analysis:" block. Add ~2-5s to the call when the VLM loads.

#### D6 — "open netflix" opened the Store / a browser instead of the app
- **Symptom.** Netflix opened the Microsoft Store "app not found"; later, a
  browser opened even though the app was installed.
- **Root cause (three layers).**
  1. `start netflix:` is a Store *protocol handler*, not the app launcher.
  2. The hardcoded AppID `Microsoft.Netflix_8wekyb3d8bbwe!Netflix` was **wrong
     for this machine**; the real one is
     `4DF9E0F8.Netflix_mcm4njqhnhss8!Netflix.App` (AppIDs vary by install
     source/version). The PowerShell launch failed silently.
  3. There was no browser fallback when the app genuinely did not open.
- **Fix.** `96baa44` generic browser fallback for known web apps;
  `09bcd80` Store/UWP apps launched via `Start-Process shell:AppsFolder\…`;
  `12e3fa9` look the **real** AppID up from `Get-StartApps` at launch time
  (hardcoded IDs are fallback only).
- **Verify.** `probe_click.py <title>` lists windows; `Get-StartApps` should
  show the app. Launch should return "Successfully opened …" and the window
  should exist.

#### D7 — `_find_window("Netflix")` matched the terminal
- **Symptom.** The probe "clicked" a terminal tab instead of Netflix.
- **Root cause.** `_find_window` does substring title matching, and a console's
  title *is its command line* (`… python probe_click.py Netflix`), so the
  console matched.
- **Fix.** `3a5ff5e`: console window classes
  (`CASCADIA_HOSTING_WINDOW_CLASS`, `ConsoleWindowClass`, `mintty`) are excluded
  from title matching unless the query is itself a terminal alias.
- **Verify.** With a console whose title contains "Netflix", `_find_window(
  "Netflix")` must return the browser, not the console.

#### D8 — THE BIG ONE: `click_element` silently no-ops on Chromium/PWA
- **Symptom.** "open netflix and open sohail profile": Netflix opened, then the
  model clicked something eight times and never opened the profile. The user
  saw it "pressing the settings button".
- **Evidence (keep this, it is the proof).**
  - Log: `click_element({'name': 'Profile 1 Profile', 'window_title':
    'Netflix'})` repeated ~8 times, alternating with
    `get_clickable_elements`.
  - `probe_click.py Netflix "Profile 1 Profile"` printed:
    `'Profile 1 Profile' (Button) … click_at(x=1150, y=35)` — **(1150,35) is
    the Edge toolbar**, and the element list contained only `Chrome Legacy
    Window`, `view_1065`, `view_1062`, `Settings and more (Alt+F)`,
    `netflix.com`. **No page content at all.**
- **Root cause (three, all real).**
  1. **Chromium hides its renderer's accessibility tree until a client asks.**
     UI Automation therefore sees only browser chrome, never the page. The only
     "Profile" the model could see was Edge's own toolbar avatar button.
  2. `click_element` preferred `GetInvokePattern()`, which Chromium reports as
     **successful while doing nothing** — so no physical click ever happened.
  3. The stall redirect fired **once** and then stayed silent, so the model
     repeated the dead click five more times.
- **Fix.**
  - `bdd0697`: web-backed controls (Chrome/Electron framework or
    `Chrome_WidgetWin` class) get a **real `pyautogui` click** at the element
    centre; native controls keep `InvokePattern`. Also the stall redirect now
    fires on **every** repeat past `_REPEAT_LIMIT`.
  - `5d9ea80`: `_nudge_accessibility()` sends `WM_GETOBJECT`/`OBJID_CLIENT` to
    the Chromium window, which flips its accessibility engine on
    (lab-proven: an example.com page went **39 → 58 named nodes** including
    body text). Wired into `get_clickable_elements`, `click_element`,
    `read_screen` via `_prepare_window_for_scan`.
  - `e14abd4`: nudge **all** same-process Chromium HWNDs (top + render-widget
    children) and foreground the window first, because a single top-HWND nudge
    was measured leaving the tree off.
- **Status: RESOLVED (mechanics) — read carefully.**
  - The **diagnosis is proven** (probe output above).
  - The **nudge is lab-proven** (39→58 nodes) but unreliable on real windows;
    it stays as a best-effort first step, NOT the route.
  - The **production route is OCR (D9) and it is live-proven**: the 2026-09-17
    run used `find_and_click_text` twice, clicked real OCR coordinates
    (`click_at` after a UIA miss), varied targets instead of looping one
    button, and reported honestly when stuck. The identical-repeat loop is
    gone.
  - **End state: Sohail's profile is active.** Proof by elimination: the gate
    screen earlier showed five profiles (Sohail, Basheer, Rehana, basherehan4,
    Aftab); the profile menu now offers exactly the other four
    (basherehan4/Basheer/Rehana/Aftab at (1109,171)/(1087,235)/(1084,301)/
    (1074,364)) plus Manage/Transfer. Sohail is the missing fourth: it is the
    active profile, so "open sohail profile" holds.
  - What closed it, beyond D9: `get_clickable_elements` now appends OCR page
    text with coordinates for Chromium windows; toolbar controls are excluded
    from Chromium scans (matching stays unfiltered, so genuine toolbar clicks
    still resolve); `find_and_click_text` is offered on click/profile
    requests; the system prompt teaches the browser-window rule; the stall
    redirect names the OCR route; OCR rank 4 matches "sohail profile" to a
    "Sohail" tile.
- **Verify (done 2026-09-17).** `probe_click.py` scan shows page text with
  coordinates; `--text "open netflix and open sohail profile"` run used OCR
  tools, no identical loop. `venv -m unittest`: 796 green.

#### D9 — OCR fallback was dead (Tesseract not installed)
- **Symptom.** `click_element`'s OCR fallback never fired; nothing to fall back
  to.
- **Root cause.** `vision.find_text_on_screen` required the Tesseract *binary*,
  which is not installed on this machine.
- **Fix.** `a1e254a`: added a RapidOCR tier (ONNX, CPU, **no binary**) between
  Tesseract and UIA. `find_text_on_screen` now ranks matches (exact >
  whole-word > prefix > substring) and accepts a `within=(l,t,r,b)` window
  filter. `find_and_click_text(target_text, window_title=…)` scopes the search
  to one window (focuses it; ignores matches outside).
- **Dep.** `rapidocr-onnxruntime` added to `requirements.txt` (installed in the
  venv; models cache on first use, ~4s/scan on CPU).
- **Verify (proven).** Live on Calculator: OCR found `C` and `1`, clicked both,
  screen read back `1`. `tests/test_ocr_click.py` (12 tests, mocked).
- **Why this matters for D8.** This is the route the user asked for: *look at
  the screen, then decide where to click.* It works regardless of who opened
  the window and regardless of Chromium accessibility.

### 15.3 Open items (not defects — unfinished verification / environment)

- **O1 — phi4 (deep tier) cannot load.** It is downloaded (9.1GB) but needs
  ~8GB free RAM; the machine has ~5GB free. **Not a code bug.** Free RAM (close
  Chrome/Edge/apps), then set `"llm_model_deep": "phi4"` in `config.json`.
  `""` = disabled. `_can_run_deep()` gates it automatically at >=6GB free.
- **O2 — End-to-end Netflix/OCR task not confirmed.** Needs a clean GUI restart
  and the 15.2 D8 verification. This is the single most important next check.
- **O3 — Live voice (`python main.py`) never run.** Only `--text` / GUI text
  input have been exercised. Do not run it in automation; hand it to the user
  with a checklist: "what time is it", "take a screenshot", "delete all my
  files" (must refuse), "open notepad and type hello".
- **O4 — `take_screenshot` latency.** The VLM analysis adds load time on first
  use. Acceptable, but if it becomes annoying, make analysis opt-in via a
  `take_screenshot(analyze=False)` parameter.

### 15.4 Traps the next agent will hit (learned the hard way)

1. **A running GUI keeps old code.** Every retest must *quit* `vave_gui.py`
   completely, not minimize it. Half the "same problem still happens" reports
   were stale processes.
2. **Use the venv**: `venv\Scripts\python.exe`. System Python has no deps.
3. **PowerShell 5.1**: no `&&`, no `rg`, and `$_` gets mangled inside
   `powershell -Command "…"` from this tool — prefer writing a script file, or
   run logic through Python.
4. **`uiautomation` sets process DPI awareness on import** (metrics jumped
   1536x864 → 1920x1080 at 125% scaling). It imports before `pyautogui` clicks,
   so coordinates agree — but do not reorder that without thinking.
5. **Chromium is a special case everywhere.** Window titles lie (a console
   contains "netflix"), page content is invisible until nudged, and
   `InvokePattern` is a no-op. Prefer OCR for anything on a web page.
6. **Never run `python main.py` bare** — it starts the mic loop and blocks.
7. **`data/`, `logs/`, `config.json` are user state.** Tests must use
   `VAVE_DATA_DIR` / `VaveTestCase`.
8. **The destructive-request guard must stay in front of the model.**
   `guard.is_destructive_request` in `ask_ai`/`run_task_step` is load-bearing;
   removing it re-opens D4.
9. **`mock.patch.dict(sys.modules, …)` deletes everything imported inside the
   region on exit** (it restores a snapshot). If `assistant.vision`
   (pytesseract → numpy) is first imported inside such a region, later
   `from assistant.vision import …` re-executes the chain and numpy's C
   extension dies with `ImportError: cannot load module more than once per
   process` — swallowed by broad excepts, surfacing as phantom "click didn't
   land" failures. Cost a full ghost-hunt on 2026-09-17. Rule: any test file
   combining `patch.dict(sys.modules)` with mocks that lazily import heavy
   modules must `import assistant.vision` eagerly at the top (see
   `tests/test_chromium_click_path.py`, `tests/test_ocr_click.py`).

### 15.5 Suggested continuation order

1. ~~Confirm **D8/O2** with a clean restart + `probe_click.py`~~ — DONE
   2026-09-17 (see D8 status; O2 end state verified by elimination).
2. ~~Write the two missing regression files~~ — DONE 2026-09-17:
   `tests/test_destructive_requests.py` (7 tests, D4 matrix + hooks) and
   `tests/test_chromium_click_path.py` (19 tests: classification, nudge,
   physical click, every-repeat stall, chrome-guard, triggers, scan entries).
   Suite: **796 green**.
3. Then build from section 14: **S3 dry-run** (safe testing) and **S5 `vave
   doctor`** (capability report) are the smallest, highest-value next steps.
4. O1 (phi4 RAM) is still blocked on free RAM (5.1GB free 2026-09-17, needs
   ~8GB) — user action, no code. O4 done (`take_screenshot(analyze=…)`).
