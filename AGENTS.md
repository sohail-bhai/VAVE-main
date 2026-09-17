# AGENTS.md

Guidance for future agents working in this repository.

## Project Scope

This is `VAVE Desktop Assistant - Version 1.2`, a Python desktop assistant. It listens through the microphone, routes recognized voice commands, speaks responses with text-to-speech, and performs local desktop actions such as opening apps/websites, changing volume, taking screenshots, reading battery state, and managing notes.

VAVE is evolving into a personal AI control plane: the user states a goal in
plain language, and VAVE coordinates AI helpers, devices, Google Workspace,
files and the web behind the scenes.

The local AI brain is implemented, not a placeholder. `assistant/ai_brain.py`
runs an Ollama-backed agent loop with tool calling over roughly 57 registered
functions. Screen awareness, persistent vector memory, wake word, phone
bridging and the safety layer (guard, confirm, audit, overwatch) all exist.

Coordination lives behind a service boundary (`assistant/control/` and
`assistant/api.py`) so the desktop app and the separately built mobile client
share one source of truth. Do not put coordination logic in the UI.

Primary user-facing capabilities:

- Voice input and spoken output.
- CustomTkinter dashboard launched with `python vave_gui.py` or `python main.py --gui`.
- Config-based personalization through `config.json`.
- App launching for common local apps.
- Website opening and Google/YouTube search.
- Time, date, battery, screenshot, lock, shutdown, and restart commands.
- Exact Windows volume control through `pycaw`, with keyboard media-key fallback.
- Simple persistent notes stored in `data/notes.txt`.

## Repository Map

- `main.py`: application entry point. Creates `AssistantController` and starts it.
- `vave_gui.py`: CustomTkinter dashboard launcher. Shows a clear install message if `customtkinter` is missing.
- `gui/`: first desktop dashboard package. GUI widgets poll `EventBus` on the main thread and must not call CustomTkinter from worker threads.
- `gui/app.py`: dashboard window, worker-thread orchestration, event polling, text command handling, smoke-test button, and voice start/stop controls.
- `gui/theme.py`: shared dashboard colors and fonts.
- `gui/widgets/`: reusable dashboard panels.
- `assistant/controller.py`: reusable assistant loop for CLI now and a future GUI later. Manages assistant state, listening, command normalization, command dispatch, and event emission.
- `assistant/state.py`: central `AssistantState` enum for lifecycle states such as idle, listening, processing, speaking, error, and stopped.
- `assistant/events.py`: queue-based event bus and event objects for future GUI-safe polling.
- `assistant/text_utils.py`: conservative recognized-text normalization helpers.
- `assistant/commands.py`: central natural-language command router. This is where most new voice commands should be added.
- `assistant/speech.py`: speech recognition and text-to-speech setup. Keeps `speak()` and `listen()` available while lazily initializing microphone/TTS dependencies.
- `assistant/smoke_test.py`: safe command-router smoke checks with browser, app, note, screenshot, volume, and destructive system side effects stubbed.
- `assistant/system_tasks.py`: OS and hardware actions, including app launch, time/date/battery, screenshot, volume, lock, shutdown, and restart.
- `assistant/config.py`: config defaults, JSON loading/merging, and setting updates.
- `assistant/notes.py`: note capture, readback, and clearing.
- `assistant/ai_brain.py`: Ollama-backed agent loop (`_agent_loop`, which takes optional `should_continue` and `authorize` hooks), the voice entry point `ask_ai()`, the control plane entry point `run_task_step()`, the tool-calling schema, and the `AVAILABLE_FUNCTIONS` tool registry.
- `assistant/control/`: the control plane. `models.py` (data model), `store.py` (SQLite), `service.py` (`ControlPlane` coordination logic), `executor.py` (`TaskExecutor`, which runs task steps through `ai_brain.run_task_step`), `capabilities.py` (namespaced capability catalog with risk levels), `policy.py` (`PolicyEngine`: allow, ask or deny), `adapters.py` (`NativeAdapter`, `HttpAdapter`, `AdapterRegistry`), `planner.py` (goal to steps), `notifier.py` (what reaches the phone, and how), `secrets.py` (encrypted credentials, resolved only inside the plane).
- `assistant/api/`: HTTP + WebSocket boundary over the control plane. `app.py` (routes), `auth.py` (device pairing, tokens, rate limiting), `errors.py` (one error envelope). Run with `python -m assistant.api`.
- `mobile/`: the separately built Expo/React Native client (another developer's tree; do not restructure it).
- `mobile/pwa/`: the dependency-free PWA the API serves at `/m` (`index.html`, `styles.css`, `app.js`, `manifest.webmanifest`, `sw.js`, icons). Pages are public; every data call carries the device Bearer token, and SSE uses `?token=` because EventSource cannot set headers. `sw.js` caches the shell only, never `/api/*`. See `docs/mobile.md`.
- `assistant/memory.py`: persistent vector memory backed by ChromaDB.
- `assistant/swarm.py`: parallel sub-agents and actor-critic research.
- `assistant/browser/`: a real Chromium driven through Playwright. `session.py` (one persistent window, numbered elements), `actions.py` (the tools the model calls). See `docs/web-agent.md`.
- `assistant/files.py`: the folders in `file_shares` reachable from a paired phone. Every path is resolved before it is checked, so `..` and symlinks out of a share are refused. See `docs/remote-files.md`.
- `assistant/web_api.py`: `web_api_get` and `web_api_call` reach any service with a REST API, resolving a named credential from the secret store. Prefer this over a per-service module.
- `assistant/site_memory.py`: per-domain notes in `data/site_notes.json`, handed back when that domain is opened again.
- `assistant/gitlab_agent.py`: GitLab over its API - issues, files, a fix on its own branch, a merge request, and merging as a separate critical step.
- `assistant/home.py`: Home Assistant over its REST API - device states and verified control actions. Reads are `home.read`, acting is `home.control` (sensitive, approval-held, REACHES_OUTWARD). Token lives in the secret store as `homeassistant`; base URL is `home_assistant_url`.
- `assistant/task_journal.py`: every high-level request/outcome in the redacted audit ledger (`record_task_attempt`, `get_weekly_failure_summary`, `recent_entries` for the GUI Journal view).
- `assistant/health_sweep.py`: daemon thread calling `plane.sweep()` on `health_sweep_seconds` cadence; only touches an existing plane; started from `bootstrap_safety()`.
- `assistant/secrets_backup.py`: passphrase-encrypted export/verify/restore of `secret.key` + encrypted vault rows (`python -m assistant.secrets_backup ...`). See `docs/secrets-backup.md`.
- `gui/tasks.py`: the desktop Web page hands what you type to the control plane and follows the real steps.
- `assistant/vision.py`, `assistant/dev_tools.py`, `assistant/calendar_sync.py`, `assistant/email_tasks.py`, `assistant/telegram_sync.py`, `assistant/wakeword.py`, `assistant/interrupter.py`: screen OCR, developer automation, Google Calendar, mail, phone bridge, wake word, global interrupt hotkey.
- `assistant/guard.py`, `assistant/confirm.py`, `assistant/audit.py`, `assistant/overwatch/`: the safety layer. Preserve these.
- `docs/control-plane.md`: control plane architecture, endpoints and limitations.
- `config.json`: user-editable runtime config.
- `data/notes.txt`: user notes data file.
- `requirements.txt`: Python dependencies.
- `README.md`: user-facing setup and command examples.
- `.gitignore`: ignores local env, Python caches, `.env`, and generated screenshot PNGs.

Ignore these during code understanding unless a task specifically targets them:

- `venv/`: checked-in virtual environment; dependency code, not application source.
- `__pycache__/` and `*.pyc`: generated Python bytecode.
- `assets/screenshots/*.png`: generated screenshots from runtime use.

## Runtime Flow

1. `main.py` parses CLI mode flags.
2. Normal mode creates `AssistantController` and calls `run_forever()`.
3. `--text` calls `handle_text_command()` once without microphone input.
4. `--once` calls `run_once()` for one microphone command.
5. `--smoke-test` runs safe command-router checks with side effects stubbed.
6. `--gui` imports the GUI lazily and launches the CustomTkinter dashboard.
7. `AssistantController.run_forever()` sets the assistant to idle, greets the user, and starts the loop.
8. The controller calls `listen()` with configured timeout, phrase-time, and pause settings.
9. The controller normalizes recognized command text with `normalize_command_text()`.
10. If speech recognition returns a non-empty command string, `execute_command(command)` handles it.
11. `execute_command()` routes in this order:
   - stop/exit/goodbye words
   - settings commands
   - volume commands
   - configured website open commands
   - YouTube search
   - Google search
   - built-in app/system commands
   - notes commands
   - AI placeholder commands
   - fallback "I do not know this command yet."

Command text is lowercased and stripped in `listen()`, so command matching assumes lowercase input.

## Configuration

`assistant/config.py` owns defaults and merges them with `config.json`. Keep new user-tunable settings in both `DEFAULT_CONFIG` and `config.json` when appropriate.

Important settings:

- `user_name`, `assistant_name`
- `voice_rate`, `voice_volume`
- `default_volume_step`
- `listen_timeout_seconds`, `listen_phrase_time_limit_seconds`, `listen_pause_threshold_seconds`
- `normal_pause_seconds`, `notes_pause_seconds`
- `normal_max_phrase_time`, `notes_max_phrase_time`
- `websites`

The `websites` object supports config-driven additions. To add another site command without code changes, add an entry such as:

```json
"websites": {
    "docs": "https://example.com/docs"
}
```

Then saying `open docs` will route through `open_website()`.

## Adding Commands

Add most new command behavior in `assistant/commands.py`.

Recommended pattern:

1. Add a small helper function if the command has parsing or multiple aliases.
2. Call that helper early in `execute_command()` if it should take precedence.
3. Keep hardware/OS side effects in `assistant/system_tasks.py`.
4. Keep data persistence in the owning module, such as `assistant/notes.py` or `assistant/config.py`.
5. Always respond through `speak()` for user-visible outcomes.

Be careful with broad substring checks. For example, `elif "time" in command` can match unexpected phrases. Prefer explicit phrases or a helper when adding commands that may overlap with existing words.

## OS and Hardware Notes

The app is designed primarily for Windows, though `system_tasks.py` contains macOS and Linux branches for some actions.

Windows-specific behavior:

- Exact volume uses `pycaw` and `comtypes`.
- App launch commands use names such as `start chrome`, `notepad`, `calc`, `code`, `explorer`, and `start cmd`.
- Lock uses `ctypes.windll.user32.LockWorkStation()`.
- Shutdown/restart use `shutdown /s /t 5` and `shutdown /r /t 5`.

Risky actions already ask for spoken confirmation in `assistant/commands.py` before shutdown, restart, and clearing notes. Preserve confirmation behavior for destructive or disruptive commands.

Avoid running the full assistant casually in automation because normal mode starts the continuous voice loop, and `listen()` requires microphone access and internet access for Google speech recognition.

## Data and Generated Files

`data/notes.txt` is user data. Do not overwrite or clear it unless the task explicitly asks for that behavior.

Screenshots are saved to `assets/screenshots/`, which may not exist until runtime. That folder is generated by `take_screenshot()`.

`config.json` is user-editable state. Changes to names or assistant settings may be made by voice commands through `update_setting()`, so treat it as runtime state as well as project config.

## Dependencies and Setup

Use the README setup flow:

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Dependencies include:

- `SpeechRecognition`
- `standard-aifc`
- `pyttsx3`
- `pyautogui`
- `pyscreeze`
- `pillow`
- `psutil`
- `PyAudio`
- `pycaw`
- `comtypes`
- `customtkinter`

PyAudio can be difficult to install on Windows. The README suggests `pipwin install pyaudio` if normal installation fails.

## Verification

There are small standard-library tests under `tests/`, plus a safe smoke-test CLI mode.

Low-risk syntax check:

```bash
python -m compileall -q main.py assistant gui vave_gui.py
```

Safe command-router smoke check:

```bash
python main.py --smoke-test
```

GUI launch for manual testing only:

```bash
python vave_gui.py
```

Do not run `python main.py` automatically during agent verification. It starts the continuous voice listening loop and can keep running or wait on microphone/TTS resources. For automated testing, use `compileall`, `--smoke-test`, or one-shot `--text "..." --no-speech` commands unless the user explicitly asks for a different runtime check.

Manual runtime check for the user:

```bash
python main.py
```

Manual runtime checks require a working microphone, speakers/TTS engine, and internet access for Google speech recognition. Give the user instructions instead of launching the assistant yourself. System commands can affect the local machine, so avoid casually testing shutdown, restart, lock, volume, and screenshot behavior without user intent.

## Current Limitations and Future Scope

Known limits in Version 1.2:

- No package metadata or installer.
- Command parsing is mostly substring matching, so overlapping phrases can misroute.
- `notes.py` assumes `data/` exists.
- Speech recognition depends on Google's online recognition service.
- Much of `system_tasks.py` is Windows-first. Volume and lock now have macOS
  and Linux backends, but window automation and some app-launch paths are still
  Windows-only. Platform-specific imports must stay lazy so the app still
  starts everywhere.
- The API authenticates remote clients with paired device tokens that expire
  (30-day default) and rotate through an audited endpoint; rate limits are
  persisted in SQLite. Remaining API gaps: notifications need a connected
  client or Telegram (no push service), and agents have no credential of their
  own. See `docs/control-plane.md` for the full list of known gaps.
- Control plane steps run through `ai_brain.run_task_step()` and can be
  interrupted between tool calls by a cancel token.
- A running step is interrupted between tool calls, not inside one. A shell
  command already running finishes first.
- A new tool must be added to `TOOL_CAPABILITIES` in `capabilities.py` with
  the right risk tier. Unmapped tools are denied inside tasks (deny by
  default); `tests/test_report_security.py` pins full coverage, so add the
  mapping or the suite fails.
- Browser work needs `pip install playwright && python -m playwright install
  chromium`. Some sites serve an empty page to a headless browser, so
  `browser_headless` defaults to false.
- `gitlab_propose_fix` sends the whole file, never a diff, and never merges.
- Adding a service usually needs no code: store a credential and use
  `web_api_get`/`web_api_call`. Write a module only when the flow needs exact
  operations, as the GitLab fix flow does.
- VAVE never types a password. A login wall is handed to the user through
  `browser_wait_for_login`.
- File access is opt-in per folder and resolved-then-checked. Never add a path
  that skips `files.resolve()`; that function is the boundary.
- A step is retried three times by default; after that the task fails.
- Resuming after a restart is not automatic: call `POST /api/tasks/resume` or
  `executor.resume_interrupted()`.
- Notifications need a connected client or Telegram; there is no push service,
  and notification history is in memory only.
- Never put a credential in a tool argument, a log or an event message. Store
  it with `plane.secrets.put()` and pass `secret://<name>`; the control plane
  resolves it when the tool runs. `data/secret.key` is not in the database and
  is not recoverable if lost.
- Agent and device health is swept on read, not by a background timer, and
  agents have no credential of their own - they call through a paired device.

Likely future work:

- Resume interrupted tasks automatically on startup instead of on request.
- Give `system_tasks.py` real cross-platform implementations.
- Add token expiry and rotation to the API.
- Make command routing more structured to reduce accidental matches.
- Add safer abstractions around app launching and destructive system commands.

## GUI Development Rules

- Use `python vave_gui.py` for manual GUI work.
- Do not start microphone listening automatically from the GUI; the user must click Start Listening.
- GUI updates must happen on the CustomTkinter main thread. Worker threads should emit events through `EventBus`, then the app should poll with `after()`.
- Prevent duplicate listener threads. Start should be disabled while voice listening is active.
- Stop should be safe when the listener is inactive.
- Text command input should not require the microphone.
- Smoke Test in the GUI must use the safe existing smoke-test path and avoid real browser/app/volume/screenshot/destructive side effects.
- Keep GUI files modular under `gui/`; do not collapse everything into `main.py`.
- Do not add web frameworks, Electron, Node, or browser-based UI layers.

## Agent Working Rules

- Keep changes small and consistent with the existing plain-Python module style.
- Prefer `rg` for searching, and exclude `venv/`, `__pycache__/`, and generated screenshot files.
- Do not edit dependency files under `venv/`.
- Do not clear `data/notes.txt` or rewrite `config.json` unless the task requires it.
- Preserve confirmation prompts for destructive or disruptive actions.
- Do not run `python main.py` automatically; provide manual runtime instructions instead.
- Use `python -m compileall -q main.py assistant gui vave_gui.py` and `python main.py --smoke-test` for safe automated checks.
- Use `python main.py --text "time" --no-speech` for one-shot command checks.
- For GUI work, use `python vave_gui.py` manually and keep all widget updates on the main thread through EventBus polling.
- Use available Codex planning, implementation, review, and critique features when they help quality without expanding scope.
- Use `assistant.controller.AssistantController` as the integration point for assistant loops.
- Use `assistant.commands.execute_command()` as the main integration point for new voice command behavior.
- Keep platform-specific behavior isolated in `assistant/system_tasks.py`.
- Update `README.md` when user-facing commands, setup steps, or config keys change.

<!-- caveman-begin -->
Respond terse like smart caveman. All technical substance stay. Only fluff die.

Rules:
- Drop: articles (a/an/the), filler (just/really/basically), pleasantries, hedging
- Fragments OK. Short synonyms. Technical terms exact. Code unchanged.
- Pattern: [thing] [action] [reason]. [next step].
- Not: "Sure! I'd be happy to help you with that."
- Yes: "Bug in auth middleware. Fix:"

Switch level: /caveman lite|full|ultra|wenyan-lite|wenyan-full|wenyan-ultra
Stop: "stop caveman" or "normal mode"

Auto-Clarity: drop caveman for security warnings, irreversible actions, user confused. Resume after.

Boundaries: code/commits/PRs written normal.
<!-- caveman-end -->
