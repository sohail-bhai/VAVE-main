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

---

## 4. Current Operational Stage

> **CURRENT STATUS: STAGE 5 (Google Workspace Cloud Sync & Verified Comms) COMPLETED**

- **Verification Status**:
  - **11/11 Smoke Tests Passing** (`python main.py --smoke-test`).
  - **All Test Suites Green** (including `test_drive_indexer.py`, `test_gmail_verified.py`, `test_google_api.py`, `test_api.py`, `test_secrets.py`, `test_store_migrations.py`).
  - **Clean Compilation**: 0 syntax/lint errors (`python -m compileall`).
- **Security Posture**:
  - Zero hardcoded secrets; credentials encrypted with AES-GCM and scoped by capability.
  - Unified emergency stop active across all interfaces.
  - Hard-deny enforcement protects codebase and operating system.

---

## 5. Next Steps & Future Roadmap

1. **Mobile Client Integration**:
   - Pair standalone mobile app (Flutter/React Native) via `/api/pair` and test WebSocket/SSE push notifications.
2. **Smart Home Expansion**:
   - Webhook bridge for IoT appliances and ambient control.
