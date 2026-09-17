# VAVE Upgrade Plan — Agent Handoff Edition

This file is the single source of truth for the upgrade work. An agent who has
never seen the conversation that produced it must be able to pick it up and
execute a phase alone. Read sections 1-3 fully before touching code.

---

## 1. Current State

- Branch `sohail`, remote `https://github.com/sohail-bhai/VAVE-main.git`.
  Latest: docs rollup (see Progress Log). Last code change: `990d71c`.
- Plan Phases 0-5 are COMPLETE and verified (artifacts + tests + wiring, one
  by one). Their detail sections were removed from this file; summaries live
  in `context.md` §3 and §12 below. Suite: **796 green** locally; CI runs
  compileall + unittest + smoke on every push.
- `context.md` holds history and the agent-trap list; `AGENTS.md` holds repo
  rules. This file holds the forward plan only.
- Remaining, in order: **7 (Service Integrations) -> 8 (Control Plane
  Enhancements)**. Stretch items are unscheduled.
- Open environment items: live voice (`python main.py`) still unverified
  (needs the user, not code). phi4 deep tier is ON HOLD — locked as future
  implementation (see O1).

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

## 4. Phase 2 — Mobile PWA Client: DONE ✅

Verified 2026-09-17 (`mobile/pwa/` served at `/m`, auth boundary, 5 tests;
`docs/mobile.md`). Details in `context.md` §3, compact record in §12.

---

## 5. Phase 3 — Reliability & Ops: DONE ✅

Verified 2026-09-17 (background sweep in `bootstrap_safety()`, secrets
backup CLI + runbook, GUI Journal filter, 15 tests). Details in `context.md`
§3, compact record in §12.

---

## 6. Phase 4 — Home Assistant Bridge: DONE ✅

Verified 2026-09-17 (`assistant/home.py`, `GET /api/home/devices`,
approval-held `POST /api/home/control`, 14 tests). Details in `context.md`
§3, compact record in §12.

---

## 7. Phase 5 — Packaging & Distribution: DONE ✅

Verified 2026-09-17 (`pyproject.toml`, `vave` subcommands, 11 unit tests,
`vave smoke` 11/11). Details in `context.md` §3, compact record in §12.

---

## 8. Phase 6 — Daily Reliability: DONE ✅

Finished 2026-09-17. Remainders implemented + tested: Tier 1
`subprocess.run` return-code check with honest failure
(`tests/test_phase6_remainders.py`), `close_app` AccessDenied report,
bare-`time` removed from `_TIME_PATTERN` ("shutdown time" → AI brain),
command snippet in `process_command` error speech. Earlier bits
(list-windows voice, notes-before-app ordering, anchored lock) were already
in. Details in `context.md` §3.

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
- **Reliability sprint (2026-09-16 → 17, `9d8ee72` → `22fa84d`)**: routing
  fast-paths, 3-tier model routing, destructive-NL guard, VLM screenshot
  analysis, deterministic chaining, Store launcher rework, Chromium a11y
  nudge, RapidOCR click route, chrome-guard + OCR scan entries,
  `tests/test_destructive_requests.py` + `tests/test_chromium_click_path.py`.
  796 green. Details in `context.md` §3.
- **Phase 6 remainders**: Tier 1 return-code check, `close_app` AccessDenied
  report, bare-`time` removal, command snippet in error speech.
  `tests/test_phase6_remainders.py` (9 tests). Suite: **806 green**.

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
| 2026-09-16/17 | Reliability sprint + defect register (§15): all D1–D9 resolved, regression files in suite (796 green). Commits `9d8ee72` → `22fa84d`. |
| 2026-09-17 | Plan cleanup: Phases 2–5 detail sections removed (verified one by one; record lives in `context.md` §3 + §12). Phase 6 rewritten to remainders (6.1, 6.3 bare-time, 6.4). §15 condensed to map + open items (O1 phi4 RAM, O3 voice). |
| 2026-09-17 | Phase 6 remainders implemented + tested (`tests/test_phase6_remainders.py`, 9 tests; 3 existing tests updated to the new intended behavior; smoke `time` → `what time is it`). Suite **806 green**. Phase 6 marked DONE. |
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

## 15. Live-Test Defect Register — RESOLVED ✅

All nine defects from the 2026-09-16 live test are fixed, unit-regressed, and
live-verified where a live check exists. The full evidence narratives lived
here during the fix session; they now live in three durable places, so this
section keeps only the map and the still-open environment items:
- `context.md` §3 (sprint record) and Agent Traps (every trap from old §15.4).
- `git log 9d8ee72..22fa84d` (every fix commit, messages carry the detail).
- The suite itself: `tests/test_destructive_requests.py` (D4),
  `tests/test_chromium_click_path.py` (D8), `tests/test_ocr_click.py` (D9).

### 15.1 Resolution map

| Defect | Resolution (commit) |
| --- | --- |
| D1 slow chaining | deterministic step tracking (`88b4853`) |
| D2 conversational tool calls | suppression + prompt rule (`174d2b9`) |
| D3 single model / GPU crash | 3-tier routing (`22d8849`, `11370fc`); phi4 ON HOLD (O1) |
| D4 destructive NL requests | request-level guard + hypothetical phrasing (`6f4845b`, `4119dd4`); `tests/test_destructive_requests.py` |
| D5 blind screenshots | VLM auto-analysis + `analyze=` flag (`81cfff0`) |
| D6 Store/app launching | real AppID lookup, hidden PowerShell, browser fallback (`96baa44`, `09bcd80`, `12e3fa9`) |
| D7 console window match | console exclusion (`3a5ff5e`) |
| D8 Chromium click loop | OCR page entries, chrome exclusion/guard, triggers, prompt rule, stall hint (`bdd0697`, `5d9ea80`, `e14abd4`, `990d71c`); Sohail verified active |
| D9 dead OCR fallback | RapidOCR tier + window scoping (`a1e254a`); live-proven on Calculator |

### 15.2 Defects (detail removed — table above, evidence in git history + suite)

### 15.3 Open items (need the user, not code)

- **O1 — phi4 deep tier: ON HOLD (locked as future implementation).**
  Downloaded (9.1GB) but needs ~8GB free RAM; the machine has ~5GB free, and
  the RAM situation is not changing. **Not a code bug — do not revisit until
  hardware changes.** When it does: free RAM, set `"llm_model_deep": "phi4"`
  in `config.json` (`""` = disabled); `_can_run_deep()` gates it
  automatically at >=6GB free.
- **O2 — DONE 2026-09-17.** Sohail verified active by elimination (see table).
- **O3 — Live voice (`python main.py`) never run.** Only `--text` / GUI text
  input have been exercised. Do not run it in automation; hand it to the user
  with a checklist: "what time is it", "take a screenshot", "delete all my
  files" (must refuse), "open notepad and type hello".
- **O4 — DONE.** `take_screenshot(analyze=…)` parameter added; default still
  analyzes.

### 15.4 Traps — moved to `context.md` Agent Traps (single source of truth).

### 15.5 Continuation — DONE except building §14 items

Items 1, 2 and 4 finished 2026-09-17 (D8/O2 verified, both regression files
in the suite at 796 green, O4 implemented). Item 3 stands: build **S3 dry-run**
then **S5 `vave doctor`** from section 14. O1 is ON HOLD, not active work.
