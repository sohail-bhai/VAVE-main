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
