# VAVE Jarvis Autonomy Roadmap

Status: **ACTIVE IMPLEMENTATION PLAN**
Created: **2026-11-03**
Target: **bounded Level-3 autonomy**
Current verified baseline: **1,118 tests OK (1 skipped), smoke 11/11**

This file is the source of truth for evolving VAVE from a capable desktop
assistant into a dependable Jarvis-like autonomous agent. Update checkboxes only
after implementation evidence exists. Keep historical implementation details in
`context.md`; keep this document focused on work still required and acceptance
gates.

---

## 1. North Star

VAVE should accept a goal rather than a script, then:

1. Understand current computer, browser, service, and task state.
2. Create a bounded plan with explicit success conditions.
3. Request only permissions genuinely needed for that plan.
4. Execute one verified action at a time.
5. Detect when an action failed or changed nothing.
6. Recover using a different strategy instead of looping.
7. Pause cleanly for login, ambiguity, approval, or missing hardware.
8. Resume after interruption or restart from persisted evidence.
9. Learn reusable procedures from successful work without storing secrets.
10. Suggest useful routines, but never silently grant itself more authority.

### Target autonomy level

The target is **bounded Level 3**, not unrestricted autonomy:

- Multi-hour goals can run without constant supervision.
- Every consequential action remains scoped, auditable, interruptible, and
  dry-runnable.
- VAVE may propose new routines or capabilities, but a person controls
  permissions and production merges.
- Emergency Stop always remains real, including during dry-run mode.

### Definition of “Jarvis-ready”

VAVE is Jarvis-ready only when all are true:

- At least 90% completion on the stable 50-scenario autonomy benchmark.
- Zero critical safety violations across benchmark and seven-day soak run.
- Every acting tool returns verified, failed, blocked, or uncertain—not an
  unverified success claim.
- Interrupted tasks resume deterministically from the last valid checkpoint.
- Duplicate retries cannot resend mail, repeat home actions, or duplicate
  uploads/commits.
- Proactive suggestions have cooldowns, relevance evidence, and one-click
  dismissal.
- No credential appears in model context, task trace, audit log, memory, or
  notification.
- The system works fully through typed GUI/CLI/Telegram when no microphone is
  present.

---

## 2. Current Foundation

Already implemented and to be preserved:

- Ollama-backed agent loop with selective tool schemas and roughly 57 tools.
- Control plane, SQLite task model, approvals, permissions, cancellation,
  API, WebSockets/SSE, phone/PWA boundary, and Telegram bridge.
- Browser automation, UIAutomation, OCR, screenshot understanding, and file
  sharing boundaries.
- Persistent vector memory, task journal, site memory, and coding lessons.
- Distributed coding workflow, Project Manager, external coding engines, and
  supervised self-evolution.
- Guard, confirmations, audit, Overwatch, Emergency Stop, credential vault,
  origin/taint tracking, and capability deny-by-default behavior.
- Process/thread lifecycle supervision, singleton locks, doctor diagnostics,
  safe smoke tests, and full regression suite.
- Consequence-free dry-run across tool, coding, project, Workspace, Home
  Assistant, Telegram governance, and remote-file service boundaries.

The next stages should improve reliability and autonomy depth, not add random
tools.

---

## 3. Verified Hardware Profile

Captured on 2026-11-03.

| Component | Verified hardware | Implication |
|---|---|---|
| OS | Windows 11 Pro 64-bit | Windows remains primary runtime target. |
| CPU | AMD Ryzen 7 7700, 8 cores / 16 threads | Strong planning, OCR, and CPU fallback capacity. |
| RAM | 16 GB DDR5-5200; about 5 GB free during scan | Avoid loading multiple large models concurrently. |
| Discrete GPU | AMD Radeon RX 7600 XT | Ollama detects ROCm device `gfx1102`. |
| Dedicated VRAM | 16,325 MB (~16 GB); 15.8 GiB seen by Ollama | Quantized 14B models are viable, subject to benchmark. |
| Integrated GPU | AMD Radeon Graphics, 512 MB dedicated | Ollama correctly drops it by default; do not enable it. |
| System drive | 447 GB SSD, ~257 GB free | Application/runtime storage is healthy. |
| Model/data drive | F: on 2 TB HDD, ~476 GB free | Models stay here; loading will be slower than SSD. |
| Secondary data drive | G:, ~877 GB free | Suitable for datasets/evaluation artifacts if needed. |
| Microphone | Realtek inputs enumerate, but no default physical input | Voice is optional; typed/GUI/Telegram are primary until hardware is added. |

### Hardware policy

- Ollama model data must remain at `F:\ollama\models`.
- Never move model blobs to C:.
- Use one large model at a time; do not keep both 14B models loaded together.
- Start with 4K context for benchmarks; permit 8K only after measured VRAM and
  latency evidence.
- Keep the integrated GPU disabled for inference.
- Lack of microphone must not make doctor or autonomous typed operation fail.

---

## 4. Ollama Inventory and Current Blocker

### Files present

`F:\ollama\models` contains about 22.7 GB with complete manifest-to-blob
references for:

- `qwen2.5:7b` (~4.36 GB model layer)
- `qwen2.5:14b` (~8.37 GB model layer)
- `deepseek-r1:14b` (~8.37 GB model layer)

`%USERPROFILE%\.ollama\models` is a junction to `F:\ollama\models`; model
bytes are not duplicated on C:.

### Running-server problem

Ollama 0.40.13 currently reports no models. Its log shows:

- Server starts with `OLLAMA_MODELS=%USERPROFILE%\.ollama\models` rather than
  direct F: path.
- All three manifests are rejected as `bad manifest` with “The system cannot
  find the path specified.”
- Every manifest references blobs that do exist, so the likely issue is the
  running desktop server using the junction/old environment rather than a
  missing 22.7 GB download.
- ROCm correctly detects RX 7600 XT (`gfx1102`) with 16 GiB VRAM.

Do not delete model blobs. Phase 0 repairs registration first and reuses
existing data.

---

## 5. Implementation Protocol

Every phase follows this sequence:

1. Select one unchecked phase or bounded subphase.
2. Write or update acceptance tests before changing behavior.
3. Run targeted tests.
4. Implement smallest architecture-consistent change.
5. Run safety/security regressions.
6. Run full suite, compileall, and smoke test.
7. Run applicable benchmark scenarios in dry-run first.
8. Record evidence and limitations in this file and `context.md`.
9. Create a focused local commit.
10. Push only after user authorization.

Rules:

- No phase may weaken guard, hard-deny, approval, taint, file-share, secret, or
  Emergency Stop boundaries.
- No test may use real send/delete/shutdown/home-control side effects.
- Never claim live success from mocked, demo, dry-run, or textual model output.
- Model output is untrusted input; schemas, paths, commands, and state
  transitions require deterministic validation.
- Coordination belongs in `assistant/control/`, not GUI widgets.
- New tools require capability mapping and security coverage.
- Each phase needs a rollback path or must be additive behind a disabled flag.

---

## 6. Phase 0 — Runtime and Model Readiness

Priority: **P0 / start here**
Dependency: none

### 0.0 Portable model-directory configuration — complete

- [x] Add validated `ollama_models_dir` setting with this desktop defaulting to
  `F:\ollama\models`.
- [x] Keep per-computer override in ignored `config.local.json` so laptop and
  desktop paths never conflict in Git.
- [x] Add Settings → AI Helpers folder picker and Save & Apply action.
- [x] Apply path to current process and Windows user `OLLAMA_MODELS` without
  moving or deleting model files.
- [x] Tell user to restart Ollama; never restart it silently.
- [x] Make doctor validate configured directory and distinguish on-disk
  manifests from models visible to running server.

### 0.1 Repair Ollama model registration

- [ ] Exit/restart Ollama desktop app after confirming user environment
  `OLLAMA_MODELS=F:\ollama\models`.
- [ ] Confirm new `server.log` reports the direct F: path, not C: junction.
- [ ] Run `ollama list` and verify all three expected tags appear.
- [ ] If manifests remain invalid, run `ollama pull` for each existing tag to
  repair metadata while reusing present blobs.
- [ ] Do not delete F: blobs unless a verified pull and backup strategy fail.
- [ ] Add doctor detail distinguishing “model files exist but server rejects
  manifests” from “model not installed.”

### 0.2 Benchmark model tiers on actual GPU

Candidate policy:

| Tier | Candidate | Intended use |
|---|---|---|
| Fast | `qwen2.5:7b` | Tool selection, short chat, routine desktop actions |
| Smart | `qwen2.5:14b` | Planning, coding review, multi-step reasoning |
| Deep/Critic | `deepseek-r1:14b` | Difficult critique and recovery only |

- [ ] Benchmark cold-load time, warm first-token latency, tokens/second, total
  VRAM, system RAM, and 4K/8K context behavior.
- [ ] Confirm `ollama ps` shows RX 7600 XT/ROCm offload rather than CPU-only.
- [ ] Verify 7B tool-call correctness against at least 20 tool-routing prompts.
- [ ] Verify 14B planner JSON validity against at least 20 planning prompts.
- [ ] Verify DeepSeek critic adds measurable value before keeping it enabled.
- [ ] Set model timeouts and keep-alive from measured evidence.
- [ ] Prevent simultaneous loading of multiple 14B models on 16 GB VRAM.

### 0.3 Make no-microphone mode first-class

- [ ] Set voice/wake-word mode off when no default microphone exists.
- [ ] Doctor treats absent microphone as warning/skipped when voice is disabled,
  not a critical system failure.
- [ ] GUI clearly shows “Text/Remote mode — no microphone configured.”
- [ ] All autonomous goals remain available from GUI, CLI, API, and Telegram.
- [ ] Keep manual enable flow ready for a future USB microphone.

### 0.4 Resolve deferred integrations

- [ ] Rotate exposed Google OAuth client secret using project-owner account.
- [ ] Download replacement `credentials.json` locally; never commit it.
- [ ] Reauthorize Workspace and confirm token lives in encrypted vault.
- [ ] Configure Telegram bot token in vault if remote control is wanted.

### Phase 0 acceptance gate

- `python main.py --doctor` has no critical failure for enabled subsystems.
- `ollama list` shows all configured tiers.
- Each selected model completes a real bounded prompt on RX 7600 XT.
- Typed assistant works with microphone absent.
- Model files remain only on F: (C: junction is acceptable only if stable).

---

## 7. Phase 1 — Deterministic Autonomy Evaluation Harness

Priority: **P0**
Dependency: Phase 0 model availability

Create `assistant/evals/` and deterministic fake environments before changing
the desktop brain.

### Tasks

- [ ] Define versioned scenario schema: setup, goal, allowed capabilities,
  expected observations, forbidden actions, success conditions, timeout.
- [ ] Build fake window/UIAutomation tree with stable controls and state
  transitions.
- [ ] Build fake OCR frames and fake Playwright pages.
- [ ] Build fake Workspace, Home Assistant, filesystem, and notification
  adapters.
- [ ] Capture complete redacted action traces.
- [ ] Add runner for one scenario, one suite, and repeated stochastic trials.
- [ ] Produce JSON and human-readable reports.
- [ ] Persist baseline metrics by commit and model tier.

### Initial benchmark families

1. Open app, locate editor, type, verify text.
2. Find a browser element and detect stale references.
3. Complete a multi-step form without submitting in dry-run.
4. Stop at login wall and request user intervention.
5. Recover from missing window or changed page.
6. Refuse destructive request outside dry-run.
7. Rehearse same destructive request with no mutation.
8. Resume interrupted multi-step task.
9. Prevent duplicate email/home/upload action on retry.
10. Complete a coding workflow without invented test success.

### Metrics

- Goal completion rate
- Verified-action rate
- Wrong-action rate
- User-intervention count
- Repeated-action count
- Recovery rate
- Median and p95 duration
- Tool calls per completed goal
- Safety-policy violations
- Secret/redaction violations

### Acceptance gate

- At least 30 deterministic scenarios run in CI without a real desktop.
- Reports compare models and commits.
- Existing behavior has a recorded baseline before Phase 2.

---

## 8. Phase 2 — Unified Grounded World Model

Priority: **P0 / highest product leverage**
Dependency: Phase 1 harness

Create `assistant/grounding/` with one truthful scene instead of unrelated
UIAutomation, OCR, browser, and screenshot answers.

### Scene contract

`perceive(window=None) -> Scene`

Each `SceneElement` includes:

- Stable scene/window/element ID
- Label and normalized label
- Role/type and available actions
- Bounding box and coordinate space
- Source (`uia`, `ocr`, `playwright`, `vision`)
- Window handle/process/domain
- Visibility, enabled, focused, and selected state
- Confidence and evidence timestamp
- Revision/hash for stale-reference detection

### Tasks

- [ ] Define typed `Scene`, `SceneWindow`, `SceneElement`, and evidence models.
- [ ] Add UIAutomation provider.
- [ ] Add OCR provider with DPI/window coordinate normalization.
- [ ] Add Playwright provider with page/frame identity.
- [ ] Add optional vision-caption provider; never invent coordinates from prose.
- [ ] Fuse duplicate elements with deterministic precedence rules.
- [ ] Generate stable IDs that survive harmless reordering but expire on real
  window/page revisions.
- [ ] Cache scenes per window/revision with bounded TTL.
- [ ] Redact password fields and sensitive values before model context.
- [ ] Replace multiple inspection-tool responses with concise scene summaries.

### Acceptance gate

- Same UI produces stable IDs across repeated perception.
- Browser chrome and page elements cannot be confused.
- DPI scaling tests cover 100%, 125%, 150%, and mixed monitors.
- Password/credential controls never expose values.
- Perception latency stays within measured desktop budget.

---

## 9. Phase 3 — Verified Action and Recovery Engine

Priority: **P0**
Dependency: Phase 2

Create one action envelope:

`act(scene_id, action, value=None) -> ActionResult`

### ActionResult contract

- `ok`
- `verified`
- `changed`
- `before_revision`
- `after_revision`
- `delta`
- `confidence`
- `failure_kind`
- `recovery_hint`
- `dry_run`

### Tasks

- [ ] Capture scene before every acting tool.
- [ ] Reject stale element IDs before input.
- [ ] Execute through existing guard and capability boundaries.
- [ ] Capture scene after action and calculate semantic delta.
- [ ] Define per-action postconditions (window opened, text appeared, state
  changed, page navigated, API object exists, etc.).
- [ ] Mark unchanged actions failed/uncertain rather than successful.
- [ ] Track attempted strategies, not only repeated raw calls.
- [ ] Route recovery to alternate provider/action once, then stop honestly.
- [ ] Add bounded undo/compensation metadata where supported.
- [ ] Collapse old click/type wrappers onto the new engine gradually.

### Acceptance gate

- No acting tool can report verified success without evidence.
- Repeated blind clicks terminate within configured bound.
- At least 90% of deterministic UI benchmark actions verify correctly.
- Failed actions name evidence and next safe recovery option.

---

## 10. Phase 4 — Durable Goal Runtime

Priority: **P1**
Dependency: verified action engine

### Tasks

- [ ] Add persistent scheduler and trigger tables to control store.
- [ ] Support time, recurrence, event, API, and manual triggers.
- [ ] Persist plan, preconditions, outputs, postconditions, and retries after
  every step.
- [ ] Auto-resume safe interrupted tasks on startup; approval-held or uncertain
  tasks enter `NEEDS_USER`.
- [ ] Add task budgets: time, steps, retries, model calls, and outward actions.
- [ ] Add idempotency keys for email, calendar, Home control, uploads, API
  writes, and commits.
- [ ] Add compensation hooks for reversible actions.
- [ ] Add `pause`, `resume`, `cancel`, `needs_user`, and `expired` semantics.
- [ ] Expose scheduler/task state consistently to GUI, PWA, API, and Telegram.
- [ ] Make Emergency Stop revoke leases and stop between every bounded action.

### Acceptance gate

- Crash/restart tests resume at correct checkpoint without duplicate effects.
- A seven-hour synthetic task survives process restart and temporary outages.
- Duplicate outward actions are impossible under retry tests.

---

## 11. Phase 5 — Permission Leases, Trace, and Audit Integrity

Priority: **P1**
Dependency: durable goal runtime

### Tasks

- [ ] Add task-scoped and time-scoped capability leases.
- [ ] Scope leases by resource/path/domain/device/recipient where applicable.
- [ ] Show active leases and countdown in GUI and phone.
- [ ] Revoke all leases on Emergency Stop, task completion, or expiry.
- [ ] Add hash-chained audit entries with backward-compatible verification.
- [ ] Add `vave trace <task>` human-readable action/evidence timeline.
- [ ] Add dry-run replay from a prior trace.
- [ ] Record idempotency and compensation evidence.

### Acceptance gate

- Lease cannot be used outside task/resource/time scope.
- Audit verifier finds first altered entry.
- Every consequential action links request, permission, tool, evidence, and
  outcome.

---

## 12. Phase 6 — Procedural and Episodic Memory

Priority: **P1**
Dependency: verified traces

### Tasks

- [ ] Define redacted `TaskTrace` schema.
- [ ] Store only completed/verified procedures by default.
- [ ] Index goal, environment, app/domain, tool versions, and outcome.
- [ ] Retrieve top-k relevant traces as suggestions, never blind commands.
- [ ] Revalidate scene IDs, paths, permissions, and tool versions before reuse.
- [ ] Add confidence, age, success count, failure count, and expiry.
- [ ] Learn recovery patterns separately from happy paths.
- [ ] Add user controls to inspect, forget, pin, and disable procedural memory.
- [ ] Generalize coding lessons into shared failure taxonomy.

### Acceptance gate

- Similar repeated tasks measurably use fewer model/tool turns.
- Stale traces fail closed and are not replayed.
- Secret-scanning tests prove no sensitive value reaches memory.

---

## 13. Phase 7 — Proactive Suggestions and Routine Mining

Priority: **P1**
Dependency: durable runtime and memory

Start suggestion-first. Do not silently automate observed behavior.

### Inputs

- Calendar timing
- Priority/unread email metadata
- Task deadlines and failure state
- Battery/disk/runtime health
- Repeated command/tool sequences
- Time of day and user-approved routines

### Tasks

- [ ] Build suggestion model with reason, urgency, expiry, and cooldown.
- [ ] De-duplicate similar suggestions across channels.
- [ ] Detect repeated verified procedures and offer routine creation.
- [ ] Add approve, dismiss, snooze, and never-suggest actions.
- [ ] Promote approved routine to scheduler with explicit permission scope.
- [ ] Measure acceptance and dismissal rates.
- [ ] Prevent proactive notifications during quiet hours unless critical.

### Acceptance gate

- No duplicate suggestion within cooldown.
- Every suggestion explains why it appeared.
- Nothing becomes an autonomous routine without explicit approval.
- Seven-day soak produces no spam burst.

---

## 14. Phase 8 — Voice and Ambient Interaction (Optional Hardware Track)

Priority: **P2 / blocked by physical microphone**

Typed GUI, CLI, phone, and Telegram remain full-featured without this phase.

### Tasks

- [ ] Add explicit modes: off, push-to-talk, wake-word, continuous.
- [ ] Detect microphone availability before starting worker.
- [ ] Add visible/listed recording state everywhere.
- [ ] Implement barge-in to stop TTS immediately when user speaks.
- [ ] Add bounded follow-up listening window.
- [ ] Add concise/detailed response mode.
- [ ] Add confidence gate for destructive words and names/recipients.
- [ ] Add speaker or local-presence checks only if justified and privacy-safe.

### Acceptance gate

- No audio capture starts silently.
- Missing microphone never harms typed autonomy.
- Destructive low-confidence speech always asks for clarification.
- Barge-in stops TTS reliably in live tests.

---

## 15. Phase 9 — Supervised Self-Evolution

Priority: **P2**
Dependency: evaluation, trace, durable runtime

### Tasks

- [ ] Every capability build runs in isolated branch/worktree.
- [ ] Generate Vision Spec, threat model, tests, and rollback before coding.
- [ ] Run dry-run and full suite against isolated worktree.
- [ ] Produce human-readable diff, evidence, and unresolved risk list.
- [ ] Require explicit approval before merge.
- [ ] Never self-modify files used by currently running process.
- [ ] Never auto-merge to main or auto-push without user command.
- [ ] Track whether implemented gaps actually solve original failed goal.

### Acceptance gate

- Failed implementation cannot alter active tree or registry to “implemented.”
- Human can review complete diff and test evidence before merge.
- Rollback restores previous verified state.

---

## 16. Phase 10 — Product Hardening and Soak

Priority: **P2**

### Tasks

- [ ] One-command setup and doctor-guided remediation.
- [ ] Configuration migration/versioning.
- [ ] Structured metrics and local diagnostics bundle with redaction.
- [ ] Startup/degraded-mode honesty for every optional subsystem.
- [ ] Seven-day continuous soak with scheduler, phone, browser, and model.
- [ ] Performance budgets for idle CPU/RAM, model load, perception, and API.
- [ ] Backup/restore drills for DB, vault key, routines, and memory.
- [ ] Signed release/update strategy.

### Acceptance gate

- Seven-day soak has no leaked worker, duplicate outward action, corrupt state,
  or critical safety event.
- Fresh-machine setup reaches healthy typed mode from documented instructions.
- Backup and restore are proven, not assumed.

---

## 17. Cross-Cutting Risk Register

| Risk | Required mitigation |
|---|---|
| Model invents success | Typed postconditions and scene/API verification |
| Prompt injection from page/file | Taint propagation, scoped tools, no secret resolution, outward confirmation |
| Duplicate outward effect | Idempotency keys and persisted result evidence |
| Stale UI coordinates | Stable scene IDs, revision checks, fresh perception |
| Endless retry loop | Strategy budget, scene-diff stall detection, honest stop |
| Secret leakage | Vault references only, redaction tests, no model/log/memory values |
| Autonomous code damages runtime | Worktree isolation, tests, human diff approval |
| Proactive spam | Relevance score, cooldown, quiet hours, dismissal memory |
| Large model exhausts memory | One large model at a time, benchmarked context, circuit breaker |
| No microphone | Typed/remote-first operation; voice remains optional |
| F: model metadata corruption | Non-destructive manifest repair; never delete blobs first |

---

## 18. Progress Ledger

| Phase | Status | Evidence |
|---|---|---|
| Foundation: control plane, safety, browser, memory | Complete | Existing suite and docs |
| Project Powerhouse and self-evolution | Complete | Existing suite and `plan.md` |
| Consequence-free dry-run | Complete | Included in 1,118-test baseline |
| Phase 0: Runtime/model readiness | **In progress** | Portable model-directory setting complete; Ollama restart/repair next |
| Phase 1: Evaluation harness | Not started | — |
| Phase 2: Grounded world model | Not started | — |
| Phase 3: Verified action/recovery | Not started | — |
| Phase 4: Durable goal runtime | Not started | — |
| Phase 5: Permission leases/audit trace | Not started | — |
| Phase 6: Procedural memory | Not started | — |
| Phase 7: Proactivity/routines | Not started | — |
| Phase 8: Voice/ambient | Blocked by microphone hardware | Typed autonomy unaffected |
| Phase 9: Supervised self-evolution | Not started | Existing version is foundation only |
| Phase 10: Product hardening/soak | Not started | — |

---

## 19. Exact Next Session

Work only on Phase 0:

1. Open Settings → AI Helpers, confirm the model directory, click Save & Apply,
   then restart Ollama.
2. Repair Ollama server path/manifest registration without deleting blobs if
   the restart still rejects manifests.
3. Confirm all three model tags appear.
4. Benchmark 7B and both 14B models on RX 7600 XT.
5. Choose measured fast/smart/deep policy and context size.
6. Make doctor treat missing microphone according to enabled voice mode.
7. Re-run full suite, smoke, compileall, and doctor.
8. Record results here before starting Phase 1.

Do not start grounding, scheduler, or proactive work until this gate passes.
