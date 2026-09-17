# VAVE Project Context & System Architecture

## 1. Vision & Overview
**VAVE** has evolved from a local desktop voice assistant (V1.2) into a **Production-Grade Personal AI Control Plane**. 

The user states high-level goals in natural language (via voice, desktop GUI, Telegram, or remote mobile/web clients), and VAVE coordinates AI helpers, devices, local tools, files, and web services behind a strict, zero-trust safety boundary.

---

## 2. Core Architecture Stack

```
                          ┌────────────────────────┐
                          │   Human Intent Channels │
                          │  Voice • GUI • Telegram │
                          │  Remote Mobile / Web    │
                          └───────────┬────────────┘
                                      │
                                      ▼
                          ┌────────────────────────┐
                          │     FastAPI + WS/SSE   │
                          │   Device Auth & Pairing│
                          │   Token Expiry/Rotation│
                          └───────────┬────────────┘
                                      │
                                      ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │                      CONTROL PLANE SERVICE LAYER                       │
 │  • ControlPlane & TaskExecutor     • PolicyEngine (Allow/Ask/Deny)    │
 │  • Task Decomposition (Planner)    • Scoped SecretStore (AES-GCM)     │
 │  • Persistent SQLite Store         • Persistent SQLite Notifier       │
 └───────┬────────────────────────────────┬────────────────────────┬──────┘
         │                                │                        │
         ▼                                ▼                        ▼
┌──────────────────┐            ┌──────────────────┐    ┌──────────────────┐
│   SAFETY LAYER   │            │   LOCAL AI BRAIN │    │  EXECUTION MESH  │
│ • Unified Stop   │            │ • Ollama qwen2.5 │    │ • Browser (Play- │
│ • Guard Hard-Deny│            │ • Agent Loop     │    │   wright)        │
│ • Audit Redactor │            │ • 57 Tools       │    │ • Dev Tools / OS │
│ • Overwatch COM  │            │ • ChromaDB RAG   │    │ • Google/GitLab  │
└──────────────────┘            └──────────────────┘    └──────────────────┘
```

### Key Subsystems:
- **Assistant Control Core (`assistant/control/`)**:
  - `service.py`: Central coordination logic for tasks, helpers, routines, devices, and plans.
  - `store.py`: SQLite-backed state store (`control.db`) managing migrations 0001 through 0014.
  - `executor.py`: `TaskExecutor` coordinating step execution through `ai_brain.run_task_step()`, tracking step timeouts, cancellations, and agent busy states.
  - `secrets.py`: AES-GCM encrypted credential vault resolving `secret://<name>` only inside the control plane with capability scoping.
  - `notifier.py`: Persistent event and notification dispatcher storing alerts in SQLite.
- **Local AI Brain (`assistant/ai_brain.py`)**:
  - Ollama-backed multi-step agent loop (`_agent_loop`) supporting tool calling across ~57 registered tools with automatic capability-scoped secret resolution.
- **Safety & Zero-Trust (`assistant/guard.py`, `assistant/confirm.py`, `assistant/audit.py`, `assistant/safety_stop.py`, `assistant/overwatch/`)**:
  - Unified Emergency Stop (`hard_stop()`), hard-deny rules protecting system integrity, append-only redacted audit logs, and rate-limited desktop screen monitoring.
- **Client Interfaces**:
  - `gui/`: CustomTkinter dark-mode dashboard with Live System Log, Dynamic Chat, Approval Modals, and Devices Token Manager.
  - `assistant/api/`: REST, WebSocket (`/ws/activity`, `/ws/events`, `/ws/notifications`), and Server-Sent Events (`/api/events/stream`) boundary.

---

## 3. Evolutionary Stages & Completed Milestones

### Phase 1–3: Desktop Foundations & Local Agent Loop (Complete)
- [x] Local LLM integration (`ollama`) with tool-calling schema.
- [x] Multi-step agent loop with screen awareness (`vision.py`, OCR, UIAutomation).
- [x] Persistent ChromaDB vector memory (`memory.py`).
- [x] Wake word (`pvporcupine`) and audio interrupt (`Ctrl+Shift+Space`).

### Phase 4: Control Plane & Multi-Device Fabric (Complete)
- [x] SQLite-backed control store with versioned migrations.
- [x] Agent helper mesh (Native, HTTP, and MCP adapters) with health monitoring.
- [x] Zero-trust capability catalog with risk tiers (`safe`, `sensitive`, `destructive`).
- [x] Device pairing workflow with SHA-256 token hashing and scoped file shares.

### Stage 4.1: Safety Hardening (Waves 0–4 Complete)
- [x] **Wave 0 (Test Isolation)**: Eliminated SQLite connection leaks and global cache contamination across unit test suites.
- [x] **Wave 1 (CRITICAL Safety Gaps)**:
  - Unified Emergency Stop (`hard_stop()`) wired to GUI "Stop Everything", `Ctrl+Alt+Shift+K`, Telegram `/kill`, and `/api/emergency-stop`.
  - Guard Hard-Deny layer unconditionally blocking edits to `assistant/*`, `gui/*`, `config.json`, `logs/*`, `data/secret.key`, and destructive terminal commands (`rm -rf`, `del /f`, `format`).
  - Overwatch auto-click safety gating through the Guard kill-switch.
- [x] **Wave 2 (HIGH Safety + Honesty)**:
  - Routine origin leak wrapped in `try ... finally` context cleanup.
  - Honest state verification in `open_app` and `click_element` (confirming real window activation before reporting success).
  - Dropped raw `"write "` typing prefix to prevent hijacking LLM queries.
- [x] **Wave 3 (Auditing & Telegram Handshake)**:
  - Subprocess cleanup in dev test loops; recursive secret redaction in audit logs.
  - Telegram 6-digit one-time pairing handshake (`/pair <code>`).
  - Multi-monitor window snapping and pending confirmation reconciliation.
- [x] **Wave 4 (Performance & Polish)**:
  - Bounded UIAutomation tree walk at 800 nodes; turn-aware conversation history trimming.
  - Secondary drive path detection for Tesseract OCR.

### Stage 4.5: Post-Hardening Production Features (Waves 5–9 Complete)
- [x] **Wave 5 (Token Expiry & Startup Auto-Resume)**:
  - Device token 30-day TTL and rotation endpoint (`POST /api/auth/rotate`).
  - Server lifespan task auto-resumption (`runner.resume_interrupted()`).
  - GUI interactive approval card integration with safe cancellation on close.
- [x] **Wave 6 (Structured Routing, Devices GUI, & SSE)**:
  - Anchored regex command router replacing loose substring matches.
  - Devices page token management UI with live expiration status and `[Rotate]` action.
  - Server-Sent Events stream (`GET /api/events/stream`) with Bearer token authentication.
- [x] **Wave 7 (Persistent Notifications)**:
  - SQLite table `notifications` (migration `0013_persistent_notifications`).
  - Persistent read/unread tracking via `GET /api/notifications` and `POST /api/notifications/{id}/read`.
- [x] **Wave 8 (Agent Busy State Lifecycle)**:
  - Helper status transitions: `idle` -> `working` with `current_task_id` during execution.
  - Guaranteed `finally` cleanup to `idle` on complete, fail, or cancel.
- [x] **Wave 9 (Capability-Scoped Secrets)**:
  - Migration `0014_secret_scopes` adding `allowed_capabilities`.
  - Secret resolution restricted to matching capability patterns (e.g. `google.*`, `telegram.*`).
  - Safety guard interception for unauthorized secret resolution during tool execution.

### Phase 5: Google Workspace Cloud Sync (Complete)
- [x] **Bi-Directional Drive Semantic Indexer**:
  - Incremental sync engine (`assistant/workspace/drive_indexer.py`) chunking Drive documents into ChromaDB vector collection `google_drive_index`.
  - SQLite table `drive_sync_state` (migration `0015_drive_sync_state`) tracking file metadata, last indexed timestamps, and chunk counts.
  - Multi-format document parser supporting Google Docs (`text/plain`), Sheets (`text/csv`), Slides (`text/plain`), and binary fallback.
  - Semantic vector search (`semantic_search_drive`) and instant export-and-index (`export_to_drive`).
- [x] **Verified Gmail Drafting & Sending**:
  - Recipient email syntax validation enforcing strict RFC address formatting.
  - Verified drafting (`draft_gmail_message`) returning confirmation payload without sending.
  - Zero-trust approval gate for sending with capability-scoped credential protection (`google.gmail.*`).
- [x] **Brain & API Gateway Integration**:
  - Tools wired to `ai_brain.py` (`sync_google_drive`, `semantic_search_google_drive`, `export_to_google_drive`, `draft_gmail_message`) with sensitivity classification.
  - REST API endpoints: `POST /api/google/drive/sync`, `GET /api/google/drive/semantic-search`, `POST /api/google/drive/export`, `POST /api/google/gmail/draft`, `POST /api/google/gmail/send`.

### Stage 5 Refinements (R1–R4 Complete)
- [x] **Store Resource Lifecycle (R1)**: Added `close()`, `__enter__`, and `__exit__` context management to `DriveIndexer` ensuring SQLite connections terminate cleanly without handle leakage.
- [x] **Strict RFC 5322 Email Validation (R2)**: Hardened `_EMAIL_RE` in `assistant/workspace/gmail.py` to prevent consecutive dots (`..`) and leading/trailing dots in local parts.
- [x] **Indexer Transactional Rollback (R3)**: `DriveIndexer` rolls back ChromaDB document insertions when SQLite `drive_sync_state` record commits fail, preventing ghost embeddings.
- [x] **Unindexable File Diagnostic Logging (R4)**: Explicit warning logged for unparseable or binary files with 0 extracted chunks.

### Stage 6A: API Hardening & Network Resilience (Complete)
- [x] **Persistent TokenBucket Rate Limiting**: Migrated in-memory rate-limiter buckets to SQLite table `rate_limits` (migration `0016_rate_limits`), persisting quotas and refill timestamps across server restarts.
- [x] **Token Rotation Audit Ledger**: SQLite table `token_rotations` (migration `0017_token_rotation_audit`) tracking every device token renewal with timestamps and requesting device ID; exposed via `GET /api/auth/audit/rotations`.
- [x] **WebSocket Heartbeats / Liveness Pings**: Added automated 25-second ping interval to `/ws/activity`, `/ws/notifications`, and `/ws/events` to prevent silent proxy drops.

### Stage 6B: Command Intelligence & Frequency Tracking (Complete)
- [x] **Command Usage Frequency Tracking**: SQLite table `command_usage` (migration `0018_command_shortcuts`) storing command pattern name, usage count, category, and last-used timestamp.
- [x] **Execution Integration**: `commands.py` records usage frequency across core voice/text command routes.
- [x] **Personalized Shortcuts Schema**: Table `command_shortcuts` providing database foundation for custom user shorthand triggers.

### Stage 8: GUI Observability & Usability (Complete)
- [x] **Real-Time System Health Bar**: Custom header bar in `HomePage` rendering CPU %, RAM %, and free disk space.
- [x] **Low Disk Warning**: Dynamic alert badge when C: drive has < 15 GB free to enforce the critical disk preservation policy.
- [x] **Drive Sync Status Card**: Live display of last Drive sync timestamp, indexed files, and chunk counts with interactive background `[Sync]` trigger button.
- [x] **Notifications [Mark All Read]**: Instant action button in `NotificationsModal` clearing all unread badges across the control plane.

### Stage 9: Developer Git Automation (Complete)
- [x] **GitLab Diff-Only Merge Requests**: Upgraded `gitlab_propose_fix()` in `assistant/gitlab_agent.py` to compute and attach unified diff blocks in MR descriptions for instant review.
- [x] **GitHub REST API Agent**: Created `assistant/github_agent.py` providing `github_list_issues`, `github_read_issue`, `github_find_file`, `github_read_file`, and `github_propose_fix`.
- [x] **Zero-Trust Classification**: GitHub tools registered in `guard.py` (read tools safe, `github_propose_fix` destructive & reaching outward) and `capabilities.py` (`github.read`, `github.write`).

### Stage 10: Cross-Platform System Layer (Complete)
- [x] **macOS Volume Backend**: Native AppleScript (`osascript`) controls in `assistant/system_tasks.py` for reading and setting volume.
- [x] **Linux Volume Backend**: Native ALSA (`amixer`) and PulseAudio (`pactl`) integration for volume get/set.
- [x] **Multi-Distro Linux Screen Lock**: Fallback chain supporting `loginctl lock-session`, `xdg-screensaver lock`, and `gnome-screensaver-command -l`.

### Stage 11: VAVE Reliability Core (Complete)
- [x] **Test Isolation Guard**: Added `get_data_dir()`, dynamic `VAVE_DATA_DIR` environment override, `reset_chroma()`, and `reset_drive_collection()` to guarantee tests never pollute user storage (`data/notes.txt`, `data/chroma_db`, `data/control.db`). Created `VaveTestCase` base class.
- [x] **Desktop Primitive Priority**: Reserved slots in `select_tools` for core atomic primitives (`list_windows`, `get_clickable_elements`, `read_screen`, `focus_window`, `close_window`, `click_element`, `click_at`, `type_text`, `press_key`, `scroll`, `wait`), preventing trivia tools from evicting control capabilities.
- [x] **OCR Fallback & Aliases**: Added OCR fallback via `vision.find_text_on_screen` when UIAutomation finds no match in `click_element`. Added semantic aliases and regex for scroll, double-click, right-click, drag-and-drop, and text search clicks.
- [x] **Honest Step Reporting & Task Journal**: Enhanced `_agent_loop` to report structured dict outcomes, ensuring `out_of_steps` marks `ok=False` in `StepResult`. Created `assistant/task_journal.py` reusing append-only redacted audit logs for failure tracking and weekly summaries.
- [x] **Centralized Safety Bootstrap**: Created `assistant/bootstrap.py` unifying global event bus, logging, audit ledger, guard, confirm, and overwatch initialization across CLI (`main.py`), GUI (`gui/app.py`), and API Server (`assistant/api/app.py`).
- [x] **Ollama Context Window Alignment**: Unified `num_ctx` default to `8192` in `ai_brain.py` matching prompt and tool definition requirements.

### Stages 12-14: Upgrade Plan Execution (Phases 0-5 Complete)

Driven by `plan.md`. Completed:

- [x] **Phase 0 — Quick Fixes, Docs Drift, CI**:
  - `disk_warning_gb` setting (default 15 GB) replaces the hardcoded 7 GB GUI threshold.
  - TokenBucket clamps negative elapsed time (NTP clock-step guard) and prunes stale `rate_limit_buckets` rows.
  - AGENTS.md / docs/control-plane.md limitation lists corrected to current truth.
  - GitHub Actions CI (`windows-latest`, Python 3.13): compileall + unittest + smoke test.
- [x] **Phase 1 — Command Intelligence Completion**:
  - Migration `0019_command_shortcuts_table` and store CRUD (save/get/list/delete/use-count).
  - Voice shortcuts: "when I say X do Y" / "delete shortcut X" / "list my shortcuts", routed before built-ins in `commands.py`.
  - Reserved-trigger guard (built-ins, media, volume, routines) and chain guard (no shortcut pointing at a shortcut).
  - REST: `GET /api/commands/frequent`, `GET|POST|DELETE /api/commands/shortcuts`.
  - Home screen suggestion chips built from real usage (`gui/integrations.frequent_command_chips`), static fallback on fresh installs.
  - GUI icon cache cleared on window destroy (`icons.reset_cache`) so repeated windows/tests render live bitmaps.
  - `playwright>=1.42.0` in requirements (1.42.0 pinned greenlet 3.0.3, which has no Python 3.13 wheels).
- [x] **Phase 2 — Mobile PWA** (`mobile/pwa/`, served at `/m`):
      login/pairing, tasks with live SSE steps, approvals with double-tap
      confirm, notifications with badges, installable shell, `docs/mobile.md`.
      Serves only `pwa/` — the sibling Expo project in `mobile/` stays off
      the web boundary.
- [x] **Phase 3 — Reliability & Ops**:
      `assistant/health_sweep.py` daemon (wired in `bootstrap_safety()`),
      passphrase-encrypted secrets backup tool
      (`python -m assistant.secrets_backup export|verify|restore`,
      `docs/secrets-backup.md`), GUI Activity "Journal" filter over
      `task_journal.recent_entries()`.
- [x] **Phase 4 — Home Assistant Bridge** (`assistant/home.py`):
      device states and verified control actions over the HA REST API;
      `home.read` (safe) and `home.control` (sensitive, approval-held,
      REACHES_OUTWARD); `GET /api/home/devices`, `POST /api/home/control`.
- [x] **Phase 5 — Packaging & Distribution**:
      `pyproject.toml` (v1.3.0), `vave` console script with subcommands
      `gui/serve/once/smoke/pair`, clean-room `pip install .` verified,
      README pip flow.

*(Note: Phase 7 Proactive AI & Morning Briefings omitted per user directive.)*

### Reliability Sprint (2026-09-16 → 17, commits `9d8ee72` → `22fa84d`)
Live-testing found real bugs; each was diagnosed with logs/probes and fixed:
- [x] **Routing fast-paths**: `_LIST_WINDOWS_PATTERN` ("list windows" voice),
  weather fast-path, notes-before-app ordering (Phase 6 items 6.2 + most of
  6.3 — `_LOCK_PATTERN` already anchored; bare-`time` alternative and the
  6.1 return-code check plus 6.4 error snippet remain open).
- [x] **3-tier model routing**: fast `qwen2.5:3b` / smart `qwen3:4b` / deep
  (RAM-gated), rule classifier, escalation with strikes (see §6).
- [x] **Safety guard for natural language**: `guard.is_destructive_request`
  blocks "delete all my files" AND hypothetical phrasing ("what would you do
  if I said…") before the model; `tests/test_destructive_requests.py`.
- [x] **Vision that sees**: `take_screenshot(analyze=…)` auto-describes via
  moondream; RapidOCR tier in `find_text_on_screen` (no Tesseract needed);
  Chromium scans list OCR page text; toolbar excluded from Chromium scans;
  chrome-guard reroutes to OCR; deterministic chaining (no per-step LLM call).
- [x] **Launcher rework**: real Store AppID lookup, hidden PowerShell launch,
  generic browser fallback, longer UWP waits.
- [x] **Regression files**: `tests/test_chromium_click_path.py` (19),
  `tests/test_ocr_click.py` (14). Suite: **796 green**.
- [x] **Proven live**: model used OCR tools, clicked real coordinates, no
  identical loop; Sohail profile verified active by elimination.

### Agent Traps (learned hard — read before debugging pointer/vision/launcher)
1. A running GUI keeps old code: retests must quit `vave_gui.py` fully.
2. Venv only: `venv\Scripts\python.exe`. PowerShell 5.1: no `&&`, no `rg`;
   `$_` gets mangled inside `powershell -Command "…"` — write script files.
3. `uiautomation` sets process DPI awareness on import (1536x864 → 1920x1080
   at 125% scaling); it imports before `pyautogui` clicks, so coordinates
   agree — do not reorder without thinking.
4. Chromium is a special case everywhere: console titles contain anything
   (substring match hit a terminal), page content is invisible until nudged,
   `InvokePattern` no-ops on web elements. Prefer OCR for page text.
5. `mock.patch.dict(sys.modules, …)` restores a snapshot on exit, deleting
   everything imported inside the region. If `assistant.vision`
   (pytesseract → numpy, whose C extension cannot init twice) is first
   imported there, later imports explode with `ImportError: cannot load module
   more than once per process` — surfacing as phantom click failures. Rule:
   test files combining it with lazy-importing mocks must
   `import assistant.vision` eagerly at the top.
6. Never run `python main.py` bare (mic loop). `data/`, `logs/`,
   `config.json` are user state — tests use `VAVE_DATA_DIR`.
7. `guard.is_destructive_request` in `ask_ai`/`run_task_step` is load-bearing.

---

## 4. Current Operational Stage

> **CURRENT STATUS: STAGES 12-14 (UPGRADE PLAN PHASES 0-5) COMPLETED**

- **Verification Status**:
  - **806 Unit Tests Green**: 100% pass across all test suites with test isolation and data guards.
  - **Clean Compilation**: 0 syntax/lint errors (`python -m compileall`).
  - **Smoke Test Verified**: 11/11 passed (`python main.py --smoke-test`).
  - **One-Shot CLI Verified**: Tested with speech suppressed.
  - **CI Green**: GitHub Actions (windows-latest, Python 3.13) runs compileall, unittest, and smoke on every push.
- **Security Posture**:
  - Zero hardcoded secrets; AES-GCM credential vault scoped by capability.
  - Unified emergency stop active across GUI, hotkeys, Telegram, and REST API.
  - Guard hard-deny enforcement and REACHES_OUTWARD classification protecting host and network.
  - Centralized safety bootstrap ensures guard, audit, and overwatch are active on every entry point.

---

## 5. Next Steps & Future Roadmap

1. **Phase 6 — DONE 2026-09-17** (remainders implemented + tested).
2. **Phase 7 — Service Integrations**: Google Calendar/Email fast-path
   commands, Telegram sync verification, secret store credential migration.
3. **Phase 8 — Control Plane Enhancements**: Network device discovery,
   proactive suggestions engine, continuous hands-free wake word mode,
   proactive notifications to phone.

---

## 6. Multi-Model Routing (Session 2026-09-15)

### What Was Built
- 3-tier model routing: fast (qwen2.5:3b) → smart (qwen3:4b) → deep (phi4)
- Rule-based complexity classifier in `select_model()` (instant, no model call)
- Conversational query suppression in `select_tools()` — no tool calls for
  greetings, math, jokes, knowledge questions
- System prompt leak detection + retry
- Auto-escalation in `chat_with_fallback()` with strike tracking
- RAM gating for phi4 (`_can_run_deep()` checks >=6GB free)
- Voice commands: "switch to fast/smart/deep/auto"

### Models on This Hardware (RTX 3050 4GB VRAM + 16GB RAM)
| Model | Size | Where | Speed | Status |
|---|---|---|---|---|
| qwen2.5:3b | 1.9GB | GPU | 3-5s | Active, fast tier |
| qwen3:4b | 2.5GB | Partial GPU | 13-67s | Active, smart tier |
| phi4 | 9.1GB | CPU only | 20-60s | Pulled, CAN'T LOAD (5GB free, needs ~8GB) |
| qwen3.5:9b | 6.6GB | Crashes GPU | 48s+ CPU | Unused |

### phi4 RAM Issue — ON HOLD (locked as future implementation, 2026-09-17)
- Model is downloaded (9.1GB) but fails to load with only 5GB free RAM
- Needs ~8GB free for weights + KV cache; the RAM situation is not changing
- Do not revisit until hardware changes. When it does: free RAM, then set
  `"llm_model_deep": "phi4"` (`""` = disabled); `_can_run_deep()` gates it

### TODO (Next Session)
1. ✅ Section 15 defects resolved with solutions
2. ✅ Regression files: `tests/test_destructive_requests.py` (7),
   `tests/test_chromium_click_path.py` (19) — suite was 796 green
3. ✅ D8 closed: OCR page entries + chrome exclusion, live-proven, Sohail active
4. ✅ O4 done: `take_screenshot(analyze=…)`; O1 phi4 locked as future work
5. ✅ Phase 6 remainders implemented + tested
   (`tests/test_phase6_remainders.py`, 9 tests) — suite **806 green**
6. Live voice test with `python main.py` (YOU need to run this)
7. Next builds when requested: S3 dry-run, S5 `vave doctor` (plan §14)

### Git State
- Branch: `sohail`, remote: `https://github.com/sohail-bhai/VAVE-main.git`
- Last commit: Phase 6 remainders (see log)
- Note: `config.json` left uncommitted (user runtime state); `test_scaffold.py`
  restored after an over-broad temp cleanup deleted it
