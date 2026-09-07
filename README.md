# ⚡ VAVE: The Personal AI Control Plane

<div align="center">

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Control Plane](https://img.shields.io/badge/Architecture-Control%20Plane-indigo.svg?style=for-the-badge)](https://github.com/sohail-bhai/vave)
[![Zero-Trust](https://img.shields.io/badge/Security-Zero--Trust%20Gate-emerald.svg?style=for-the-badge)](https://github.com/sohail-bhai/vave)
[![Local LLM](https://img.shields.io/badge/AI-Ollama%20%7C%20Local%20First-orange.svg?style=for-the-badge&logo=ollama&logoColor=white)](https://ollama.ai)
[![Framework Agnostic](https://img.shields.io/badge/Ecosystem-Framework%20Agnostic-cyan.svg?style=for-the-badge)](https://github.com/sohail-bhai/vave)

<p align="center">
  <b>The Unified Operating Layer for AI Helpers, Multi-Device Execution, Google Workspace, and Autonomous Web Workflows.</b>
</p>

> *"The user thinks in goals. VAVE handles agents, devices, tools, permissions, services, and execution."*

[Official Docs](docs/PROJECT_SUMMARY.md) • [Explore Architecture](#-system-architecture) • [Key Capabilities](#-core-capabilities) • [Live Demo Workflow](#-the-3-minute-demo-workflow) • [Zero-Trust Security](#-zero-trust-safety--human-in-the-loop) • [Quickstart](#-quickstart-guide)

---

![VAVE AI Control Plane Dashboard](assets/vave_dashboard_mockup.jpg)    

</div>

---

## 🎯 Vision & Executive Summary

Most existing AI tools force users to act as **infrastructure managers**: switching between isolated chat windows, pasting API keys, managing Python environments, running separate agent frameworks, and manually synchronizing files across computers, phones, and Google Drive.

**VAVE fundamentally flips this paradigm.** 

VAVE is not merely a voice chatbot, a script runner, or an OpenClaw frontend. It is a **Personal AI Control Plane**—a single, elegant, and secure command interface that translates high-level human intent into coordinated, multi-agent execution across every machine, service, and data source you own.

```
                  ┌─────────────────────────────────────────────────┐
                  │                 USER (High-Level Intent)        │
                  │   "Prepare my Hackwave project and run tests"    │
                  └────────────────────────┬────────────────────────┘
                                           │
                                           ▼
                  ┌─────────────────────────────────────────────────┐
                  │           ⚡ VAVE AI CONTROL CORE             │
                  │   • Intent Resolution  • Task Decomposition    │
                  │   • Shared Semantic RAG • Zero-Trust Gate       │
                  └──────┬─────────────────┬─────────────────┬──────┘
                         │                 │                 │
                         ▼                 ▼                 ▼
             ┌──────────────────────┐ ┌───────────────┐ ┌──────────────────────┐
             │   AI HELPER MESH     │ │ DEVICE FABRIC │ │ SERVICES & ECOSYSTEM │
             │ Native • OpenClaw    │ │ Desktop / PC  │ │ Google Workspace     │
             │ LangGraph • CrewAI   │ │ Cloud GPU     │ │ Drive, Gmail, Docs   │
             │ MCP Agent Protocols  │ │ Mobile / Phone│ │ Autonomous Web / OS  │
             └──────────────────────┘ └───────────────┘ └──────────────────────┘
```

---

## 🏛️ System Architecture

VAVE is architected as a modular, resilient, and framework-agnostic control fabric:

<div align="center">

![VAVE Technical Architecture Diagram](assets/vave_architecture_diagram.jpg)

</div>

### Architectural Layers

| Layer | Component | Description |
| :--- | :--- | :--- |
| **1. Goal Ingestion** | **Omni-Channel Interface** | Accepts human goals via natural voice (`pvporcupine` + `pyttsx3`), desktop GUI (`customtkinter`), remote Telegram bots, or CLI. |
| **2. Control Core** | **Brain & Intent Engine** | Powered by local LLMs (Ollama `qwen2.5`) with multi-step recursive reasoning, dynamic tool selection, and short/long-term ChromaDB vector memory. |
| **3. Safety & Policy** | **Zero-Trust Guard (`guard.py`)** | Strict interception boundary evaluating every tool call against permission tiers (`safe`, `sensitive`, `destructive`). Emits append-only redacted audit logs. |
| **4. Helper Network** | **Framework-Agnostic Mesh** | Coordinates native sub-agents, OpenClaw nodes, LangGraph pipelines, CrewAI swarms, and MCP servers without vendor lock-in. |
| **5. Device Fabric** | **Cross-Machine Execution** | Dynamically routes workloads to local desktop, remote cloud GPUs, secondary laptops, or mobile endpoints based on task needs. |
| **6. Workspace Layer** | **Google Ecosystem Engine** | First-class OAuth 2.0 integration with Google Drive (semantic search), Gmail (draft/summary), Calendar, and Docs/Sheets. |
| **7. Physical Layer** | **Overwatch & Vision Engine** | Real-time UIAutomation & PyAutoGUI scanning with rate-limited autonomous background screen inspection and auto-approval rules. |

---

## 🚀 Core Capabilities

### 1. 🤖 Framework-Agnostic AI Helper Mesh
Never be locked into a single AI agent ecosystem. VAVE abstracts agent frameworks into a unified **AI Helper Registry**:
- **Capability-Based Discovery**: Don't pick servers or protocols—tell VAVE *"Train this model"*, and it automatically discovers and assigns the ML Helper.
- **Inter-Helper Handoff**: A coding helper needing a GPU passes context to an ML Helper on a remote machine, collects the output, and continues its task.
- **Actor-Critic Research Swarms**: Spawns concurrent research, review, and verification sub-agents that autonomously critique and refine deliverables.

### 2. 🛡️ Zero-Trust Safety & Human-in-the-Loop
Autonomous agents should never have blanket access to your life or filesystem.
- **Task-Scoped Micro-Permissions**: Temporary grants (e.g., Read + Write limited to `project/` for 30 minutes).
- **Interactive Approval Modals**: Destructive or consequential actions (file modifications, code commits, database operations, emails, terminal execution) pause execution until approved via GUI card or Telegram prompt.
- **Hardware Emergency Kill-Switch**: Instantly freeze all active tasks and revoke all temporary credentials at any moment with **`Ctrl+Alt+Shift+K`**.
- **Tamper-Evident Audit Stream**: Every action is redacted of secrets and logged in real-time to an append-only audit ledger (`logs/audit.jsonl`).

<div align="center">

![Zero-Trust Human Approval Modal](assets/vave_approval_flow.jpg)

</div>

### 3. 👁️ Autonomous Overwatch Engine
Built for intense multi-tasking workflows (e.g. hackathons, CI builds, software compilation):
- **Background Screen Monitor**: Continuously monitors designated active windows (e.g., IDE build popups, terminal confirmations) without stealing mouse focus.
- **Intelligent Auto-Approval**: Evaluates screen state against configured policy rules to automatically acknowledge non-critical progress prompts (e.g., clicking *"Proceed"*, *"Submit"*, *"Yes"*).
- **Z-Order Aware & Rate-Limited**: Strictly limits action velocity to prevent runaway clicks and verifies window hierarchy in real-time.

### 4. 🌐 Unified Google Workspace & Cross-Device Fabric
- **Natural Language Workspace Actions**: *"Find my Hackwave presentation and turn the research notes into a Google Doc."*
- **Cross-Device File Resolution**: Searches across local drives, secondary laptops, connected storage, and Google Drive simultaneously.
- **Context-Preserving Remote Sync**: Seamlessly transitions execution from your desktop to your mobile device via Telegram without losing state.

### 5. 📜 The "System Log": Observable, Non-Opaque AI
Say goodbye to cryptic terminal outputs and dangerous hallucinated hidden reasoning:
- The signature right-side **System Log** renders real-world, observable actions in clean, plain English:
  ```text
  [12:34:01] [INFO]   Looking through your local files for "Hackwave presentation"...
  [12:34:03] [INFO]   Found 3 matching project documents.
  [12:34:04] [INFO]   Searching connected Google Drive for supplementary slides...
  [12:34:06] [TOOL]   Activating Web Research Helper for latest market data...
  [12:34:10] [WARN]   VAVE needs your approval: Ready to commit 14 modified files.
  ```

### 5. 🎨 Minimalist Control Dashboard
A completely redesigned, true dark-mode CustomTkinter interface inspired by premium tools like ChatGPT and Linear.
- **Dynamic Conversational UI**: Starts as an ultra-minimal void. Upon interaction, morphs into an auto-scrolling chat window with clean right/left aligned message bubbles.
- **Native In-App Overlays**: Popups like Notifications, Command Palettes, and Approval Modals render natively as floating frames inside the app, avoiding cluttered OS windows.
- **Live System Log Dual-View**: Your natural conversation stays centered in the chat view, while VAVE's internal background thoughts and actions stream independently into the right-hand System Log.

---

## ⏱️ The 3-Minute Demo Workflow

Experience how VAVE turns a high-level goal into reality:

```mermaid
sequenceDiagram
    autonumber
    actor User as 👤 User
    participant J as ⚡ VAVE Control Core
    participant W as 🤖 AI Helpers (Coder/Researcher)
    participant D as 💻 Device & Google Workspace
    participant S as 🛡️ Zero-Trust Gate

    User->>J: "Continue my Hackwave project and run the build"
    Note over J: Decomposes Goal into Tasks & Checks Permissions
    J->>W: Assign Task: Retrieve files & Analyze dependencies
    W->>D: Search Local Drive & Google Drive
    D-->>W: Returns Hackwave files & API specs
    W->>D: Execute Test Suite & Build Verification
    D-->>W: Tests Passed (14 files ready to commit)
    W->>S: Request Approval for Git Commit & Remote Push
    S->>User: ⚠️ Interactive Approval Dialog (What, Why, Diff)
    User->>S: Click [Approve & Execute]
    S->>D: Git Commit & Push Changes
    J-->>User: "✓ Hackwave project updated and verified!"
```

---

## 🕹️ Comparison: Why VAVE Wins

| Capability | Traditional Chatbots | Single-Agent Tools | OpenClaw Alone | ⚡ VAVE Control Plane |
| :--- | :---: | :---: | :---: | :---: |
| **Paradigm** | Question / Answer | Single Task Execution | Gateway / Node Transport | **Goal-Oriented Control Plane** |
| **Framework Independence** | ❌ None | ❌ Framework Locked | ❌ Custom Protocol | **✅ Agnostic (OpenClaw/CrewAI/MCP)** |
| **Multi-Device File & Execution** | ❌ No | ❌ Host Only | ⚠️ Manual SSH/Transfer | **✅ Automated Cross-Device Mesh** |
| **Google Workspace Native** | ❌ No | ⚠️ Partial Plugins | ❌ No | **✅ Full Workspace OAuth Fabric** |
| **Human-in-the-Loop Safety** | ❌ No | ⚠️ Basic Prompts | ❌ Host Level | **✅ Zero-Trust Gate + Hard Kill-Switch** |
| **Autonomous UI Overwatch** | ❌ No | ❌ No | ❌ No | **✅ Real-Time Screen Auto-Action** |
| **Observable Activity UI** | ❌ Terminal Logs | ❌ Opaque CoT | ❌ CLI Output | **✅ Plain-English System Log** |

---

## ⚡ Quickstart Guide

### Prerequisites
- **OS**: Windows 10/11 (or macOS / Linux with core feature set)
- **Python**: 3.10 to 3.12 recommended
- **Local AI Engine**: [Ollama](https://ollama.ai) installed with `ollama run qwen2.5:3b`

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/sohail-bhai/vave.git
cd vave

# Create and activate a clean virtual environment
python -m venv venv
venv\Scripts\activate      # On Windows
# source venv/bin/activate # On Linux/macOS

# Install dependencies
pip install -r requirements.txt
```

### 2. Configuration (`config.json`)
Personalize names, models, and safety levels directly in `config.json`:

```json
{
  "user_name": "Sohail",
  "assistant_name": "Vave",
  "llm_model": "qwen2.5:3b",
  "wake_word_enabled": true,
  "safety": {
    "voice": { "confirm_destructive": true },
    "telegram": { "confirm_sensitive": true, "confirm_destructive": true }
  },
  "overwatch": {
    "max_z_order": 3,
    "scan_interval": 1.0,
    "rules": [
      { "target_text": "submit", "auto_click": true, "pattern": "exact" },
      { "target_text": "proceed", "auto_click": true, "pattern": "exact" }
    ]
  }
}
```

### 3. Launching VAVE

#### 🖥️ Launch the Modern Desktop Control Dashboard
```bash
python vave_gui.py
```
*Access the ultra-minimalist, dark-mode ChatGPT-style interface with native in-app overlays, a dynamic conversational chat layout, Live System Log, and single-click task approvals.*

#### 🎙️ Launch in Continuous Headless / Voice Mode
```bash
python main.py
```

#### 🌐 Launch the Control Plane API & WebSocket Server
Exposes the control plane over HTTP and WebSockets for multi-device sync (desktop, mobile, cloud):
```bash
./run-server.sh                 # Localhost only
./run-server.sh --host 0.0.0.0  # Reachable from mobile/network
./run-server.sh --pair          # Print a pairing code for a phone
```
`run-server.sh` uses the interpreter in `./venv`, where the dependencies are
installed. Calling `python -m assistant.api` from a shell that has not run
`source venv/bin/activate` fails with `No module named 'fastapi'`.
- **Interactive Swagger Documentation**: `http://127.0.0.1:8765/docs`
- **Real-Time Streams**: `ws://127.0.0.1:8765/ws/activity` (everything),
  `/ws/events` (filterable by type and task), `/ws/notifications` (only what needs you).
- **Execute Goals via API** — tasks are not just tracked, they are worked. Each
  step runs through the same tools and safety guard as the voice loop:
  ```bash
  curl -X POST http://localhost:8765/api/tasks \
       -H 'Content-Type: application/json' \
       -d '{"goal": "Summarize my notes and check battery", "autoplan": true, "run": true}'
  ```
  `autoplan` lets VAVE break the goal into steps; independent steps run at
  the same time, and a running step can be stopped mid-flight.
- Full API data model and endpoint reference: [`docs/control-plane.md`](docs/control-plane.md).

#### 🔐 Pairing a Device
Reaching the port is not the same as being allowed to use it. Remote clients
pair once and then carry their own revocable token:
```bash
curl -X POST localhost:8765/api/pair/code        # on this computer
curl -X POST http://<computer>:8765/api/pair \
     -H 'Content-Type: application/json' \
     -d '{"code": "418302", "name": "My phone", "kind": "phone"}'
```
Only the SHA-256 hash of a token is stored, and `DELETE /api/devices/{id}/token`
revokes one device without disturbing the others.

#### 📱 Phone to Desktop Agent Work
Run VAVE on the desktop or server that owns the model, browser, files and OS tools:
```bash
python main.py --server --host 0.0.0.0 --port 8765
```
The server prints an address such as `192.168.1.20:8765`. Enter that in the
phone app, then mint a one-time pairing code on the desktop:
```bash
python -m assistant.api --pair --port 8765
```
After pairing, the phone command box posts goals to `/api/tasks` with
`autoplan=true` and `run=true`. Work executes on the desktop/server through
`TaskExecutor` and `assistant.ai_brain.run_task_step()`, while the phone follows
progress through `/ws/events` and answers approvals through `/api/approvals`.

#### 📁 Your Files, From Anywhere
Share folders with your phone and reach them from a train:
```json
"file_shares": ["~/Documents", "~/Pictures"]
```
```bash
curl -H "Authorization: Bearer $TOKEN" localhost:8765/api/files
curl -H "Authorization: Bearer $TOKEN" \
     "localhost:8765/api/files/download?path=reports/q3.txt" -o q3.txt
```
Only the folders you list are reachable, every path is resolved before it is
checked, and each transfer is written to the timeline with the device that
asked. See [`docs/remote-files.md`](docs/remote-files.md).

#### 🗝️ Credentials
Secrets live in an encrypted store rather than in `config.json`. Agents receive
`secret://<name>` and never the value — the control plane resolves it at the
moment a tool runs:
```bash
curl -X POST localhost:8765/api/secrets/import-config   # move existing ones
```
The encryption key lives outside the database (`data/secret.key`, or
`VAVE_SECRET_KEY`), so a copied `control.db` is not a copied set of
credentials. Back it up separately.


#### 🛡️ Safe Verification Modes
```bash
# Run isolated smoke test (stubs desktop side effects)
python main.py --smoke-test

# Test single command without speech
python main.py --text "what time is it" --no-speech
```

---

## ⌨️ Emergency Keyboard Shortcuts

| Shortcut | Action | Scope |
| :--- | :--- | :--- |
| <kbd>Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>Space</kbd> | **Interrupt Speech** | Instantly silences VAVE TTS speech output. |
| <kbd>Ctrl</kbd> + <kbd>Alt</kbd> + <kbd>Shift</kbd> + <kbd>K</kbd> | **GLOBAL KILL-SWITCH** | Freezes all active tasks, revokes temporary tokens, and blocks all tool calls. |

---

## 🗺️ Engineering Roadmap

- [x] **Phase 0: Foundations** — Bounded event bus, unified logging, append-only JSONL audit ledger.
- [x] **Phase 1: Safety Gate** — Context-aware permissions (`guard.py`), unified prompt broker (`confirm.py`).
- [x] **Phase 2: Autonomous Overwatch** — COM-initialized background UIAutomation scanner with rule matching and rate limiting.
- [x] **Phase 3: Control Dashboard** — Ultra-minimalist ChatGPT-style dark mode UI, native in-app overlays, Live System Log, and dynamic confirmation cards.
- [x] **Phase 4: Helper Network Fabric** — Agent registry with health and a kill switch, plus native and HTTP adapters that MCP, LangGraph and containerised agents plug into.
- [ ] **Phase 5: Google Workspace Cloud Sync** — Full bi-directional Drive semantic indexer and Gmail action drafting.

---

## 👥 Contributors & Acknowledgments

Engineered with passion for the Hackathon by **Sohail & Team**. Special thanks to the open-source communities behind **Ollama**, **CustomTkinter**, and **UIAutomation**.

<div align="center">
  <sub>Built for the future of human-agent collaboration.</sub>
</div>
