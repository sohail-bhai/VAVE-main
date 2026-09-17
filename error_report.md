# VAVE Desktop Assistant & Control Plane: Current Error & Audit Report

**Date**: 2026-09-17  
**Scope**: `assistant/`, `gui/`, `config.json`, Core Architecture & Protocols  
**Status**: Fresh Deep Audit of Current Clean Codebase (16 Unresolved Findings across 5 Distinct Domains)

---

## Executive Summary Matrix

| ID | Domain | Severity | Confidence | Location | Summary |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **SEC-01** | Security | **Critical** (CVSS 9.6) | 100% | [`assistant/api/app.py:L298-L317`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/api/app.py#L298-L317) | Global CSRF / Origin validation missing on state-changing API endpoints enables remote computer takeover |
| **SEC-02** | Security | **High** (CVSS 8.2) | 100% | [`assistant/api/app.py:L1307-L1425`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/api/app.py#L1307-L1425) | Cross-Site WebSocket Hijacking (CSWSH) on event, notification, and activity streams exfiltrates live data |
| **SEC-03** | Security | **High** (CVSS 8.1) | 100% | [`assistant/control/capabilities.py:L80-L165`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/control/capabilities.py#L80-L165) | 16 tools missing from capability catalog auto-grant permission, bypassing policy and approval gates |
| **SEC-04** | Security | **High** (CVSS 7.8) | 95% | [`assistant/guard.py:L120-L170`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/guard.py#L120-L170) | Hard-invariant gap permits overwriting `control.db` and reading root `.env` credential files |
| **REL-01** | Reliability | **Critical** | 100% | [`assistant/system_tasks.py:L542-L580`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/system_tasks.py#L542-L580) | Empty or short process name in `close_app` triggers mass termination of all Windows OS processes |
| **REL-02** | Reliability | **High** | 100% | [`assistant/control/service.py:L759-L764`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/control/service.py#L759-L764) | Resolving one approval prematurely marks multi-approval tasks as running while remaining approvals pending |
| **REL-03** | Reliability | **Medium** | 95% | [`assistant/api/auth.py:L59, L88-L97`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/api/auth.py#L59) | Unbounded memory growth in rate limiter in-memory token bucket without TTL pruning |
| **REL-04** | Reliability | **Medium** | 100% | [`assistant/files.py:L236-L255`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/files.py#L236-L255) | Windows reserved device names (`CON`, `NUL`, `AUX`) and second-precision collisions in `save_upload` |
| **PERF-01** | Performance | **High** | 100% | [`assistant/control/notifier.py:L82-L97`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/control/notifier.py#L82-L97) | Synchronous outbound Telegram HTTP requests block control plane event subscriber pipeline |
| **PERF-02** | Performance | **Medium** | 95% | [`assistant/control/secrets.py:L179-L197`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/control/secrets.py#L179-L197) | Redundant SQLite queries and Fernet decryptions on every single timeline event and log line |
| **PERF-03** | Performance | **Medium** | 90% | [`assistant/browser/session.py:L86-L93`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/browser/session.py#L86-L93) | Indefinite future wait on browser thread worker causes unrecoverable task hang on Playwright stall |
| **ARCH-01** | Architecture | **Medium** | 95% | [`assistant/api/app.py:L435-L451`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/api/app.py#L435-L451) | Missing client device attribution stamps all remote tasks as originating from local computer |
| **ARCH-02** | Architecture | **Medium** | 90% | [`assistant/site_memory.py:L53-L59`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/site_memory.py#L53-L59) | Non-atomic write in `SiteMemory` risks file truncation and data loss during process crash |
| **EDGE-01** | Edge-Case | **High** | 100% | [`assistant/commands.py:L165-L187`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/commands.py#L165-L187) | Overly greedy substring match in `handle_volume_command` intercepts general math/science questions |
| **EDGE-02** | Edge-Case | **Medium** | 100% | [`assistant/commands.py:L74-L91`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/commands.py#L74-L91) | Unanchored phrase match in `change_assistant_name` renames assistant during natural conversation |
| **EDGE-03** | Edge-Case | **Low** | 95% | [`assistant/system_tasks.py:L1490-L1505`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/system_tasks.py#L1490-L1505) | Subprocess execution lacks `stdin=subprocess.DEVNULL`, causing 15s hang on interactive CLI commands |

---

## 1. Security Review

### SEC-01: Global CSRF & Origin Validation Gap Across State-Changing API Endpoints
* **Severity**: Critical (CVSS 9.6 - `AV:N/AC:L/PR:N/UI:R/S:C/C:H/I:H/A:H`)
* **Confidence**: High (100%)
* **Location**: [`assistant/api/app.py:L298-L317`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/api/app.py#L298-L317)
* **Description**:
  In `authenticate_and_limit` middleware, `guard.check_same_origin(request)` is strictly applied to pairing routes (`/api/pair` and `/api/pair/code`). All other mutation endpoints—including `/api/tasks` (task creation & execution), `/api/approvals/{id}/resolve`, `/api/files/upload`, `/api/home/control`, `/api/permissions`, and `/api/secrets`—completely omit origin checks.
  Because `config.json` specifies `api_trust_localhost: true` by default, requests originating from the loopback interface (`127.0.0.1`) are treated as authenticated. When a user navigates to any third-party malicious website in Chrome or Edge, attacker JavaScript can trigger cross-origin POST requests directly to `http://127.0.0.1:8765/api/tasks`. Because `Origin` is not validated, the loopback trust grants access, creating and executing arbitrary autonomous tasks that run terminal commands and take over the user's desktop.
* **Fix**:
  Apply `guard.check_same_origin(request)` universally to all state-changing HTTP verbs (`POST`, `PUT`, `PATCH`, `DELETE`) for unauthenticated loopback requests:
  ```python
  if request.method in ("POST", "PUT", "PATCH", "DELETE"):
      if not bearer_token(request.headers.get("authorization")):
          guard.check_same_origin(request)
  ```
* **Reproduction Steps**:
  1. Start the API server: `python -m assistant.api`.
  2. Open an external site (or local HTML file `file:///...` or `http://attacker-site.com`).
  3. Open DevTools Console and execute:
     ```javascript
     fetch("http://127.0.0.1:8765/api/tasks", {
       method: "POST",
       headers: {"Content-Type": "application/json"},
       body: JSON.stringify({goal: "open calc", autoplan: true, run: true})
     });
     ```
  4. Notice the task is accepted and runs without authentication.

---

### SEC-02: Cross-Site WebSocket Hijacking (CSWSH) on Live Event & Notification Streams
* **Severity**: High (CVSS 8.2 - `AV:N/AC:L/PR:N/UI:R/S:U/C:H/I:N/A:N`)
* **Confidence**: High (100%)
* **Location**: [`assistant/api/app.py:L1307-L1425`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/api/app.py#L1307-L1425)
* **Description**:
  The WebSocket endpoints (`/ws/events`, `/ws/notifications`, `/ws/activity`, and `_stream`) authenticate connections via `guard.authenticate(token, websocket.client.host)`. When connecting from a local browser, `token` is empty and `client.host` is `127.0.0.1`, so `authenticate` allows the connection through loopback trust.
  Crucially, none of the WebSocket handlers inspect or validate the incoming `Origin` HTTP header. Browsers automatically transmit `Origin: https://evil.com` upon initiating WebSocket connections. Attacker JavaScript on any website can open a WebSocket to `ws://127.0.0.1:8765/ws/events`, bypass authentication via localhost trust, and stream real-time task outputs, OCR text, notification payloads, and credentials in transit.
* **Fix**:
  Extract and validate the `Origin` header before calling `await websocket.accept()`. If `Origin` is present and does not match the server host, require an explicit valid device Bearer token:
  ```python
  origin = websocket.headers.get("origin", "")
  if origin and not token:
      try:
          from urllib.parse import urlparse
          origin_host = (urlparse(origin).hostname or "").lower()
          server_host = (websocket.url.hostname or "").lower()
          if origin_host != server_host:
              await websocket.close(code=1008)
              return
      except Exception:
          await websocket.close(code=1008)
          return
  ```
* **Reproduction Steps**:
  1. Start the API server: `python -m assistant.api`.
  2. Open Chrome to `https://example.com` and open DevTools Console.
  3. Run:
     ```javascript
     const ws = new WebSocket("ws://127.0.0.1:8765/ws/events");
     ws.onmessage = (e) => console.log("Leaked Event:", e.data);
     ```
  4. The WebSocket connects immediately (code 101 Switching Protocols) and streams internal system activity events to the external domain.

---

### SEC-03: Unmapped Tools in Capability Engine Auto-Grant Administrative Access
* **Severity**: High (CVSS 8.1 - `AV:L/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H`)
* **Confidence**: High (100%)
* **Location**: [`assistant/control/capabilities.py:L80-L165`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/control/capabilities.py#L80-L165), [`assistant/control/executor.py:L372-L375`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/control/executor.py#L372-L375)
* **Description**:
  In `assistant/control/capabilities.py`, `TOOL_CAPABILITIES` maps registered tool functions to control plane capability strings. Currently, 16 tools defined in `assistant/ai_brain.py:AVAILABLE_FUNCTIONS` are missing from `TOOL_CAPABILITIES`:
  `open_app`, `close_app`, `close_window`, `focus_window`, `press_hotkey`, `write_to_screen_line`, `create_google_slides`, `set_volume`, `mute_volume`, `list_windows`, `tell_time`, `tell_date`, `tell_battery`, `get_weather`, `wait`, `propose_new_feature`.
  In `TaskExecutor._authorizer(task_id, agent, token)`:
  ```python
  def authorize(tool_name):
      capability = capability_for_tool(tool_name)
      if not capability:
          return True, ""  # AUTO-GRANTED!
  ```
  Because `capability_for_tool` returns `""` for unmapped tools, tools capable of killing processes (`close_app`), launching arbitrary system commands (`open_app`), or sending global keystrokes (`press_hotkey`) are automatically granted without policy evaluation or human approval.
* **Fix**:
  1. Map every registered tool in `TOOL_CAPABILITIES` to its respective capability:
     - `open_app`: `system.action`
     - `close_app`: `system.power`
     - `close_window`, `focus_window`, `list_windows`: `system.window.manage`
     - `press_hotkey`, `write_to_screen_line`: `system.input.control`
     - `create_google_slides`: `google.drive.write`
     - `set_volume`, `mute_volume`: `system.action`
  2. Modify `_authorizer` in `executor.py` so that unmapped tools are denied or default to high-risk approval rather than being silently allowed.
* **Reproduction Steps**:
  1. In a task step, issue an instruction that triggers `close_app` or `press_hotkey`.
  2. Inspect the authorization flow in `TaskExecutor._authorizer`.
  3. The authorizer immediately returns `(True, "")` without asking the policy engine or creating an approval request.

---

### SEC-04: Hard-Invariant Gap Permits Overwriting `control.db` and Reading `.env`
* **Severity**: High (CVSS 7.8 - `AV:L/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:N`)
* **Confidence**: Medium-High (95%)
* **Location**: [`assistant/guard.py:L120-L170`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/guard.py#L120-L170)
* **Description**:
  In `assistant/guard.py`, `_is_hard_denied` protects against mutating `assistant/*`, `gui/*`, `logs/*`, `config.json`, and `secret.key`. However, `control.db` (the SQLite database holding tokens, permissions, policies, and audit logs) and root `.env` (holding environment secrets) are omitted from the mutation blacklist.
  An autonomous agent calling `write_file(filename="data/control.db", content="...")` or `write_file(filename=".env", content="...")` bypasses the invariant.
  Additionally, `is_protected_read_path` only checks `_PROTECTED_READ_NAMES = ("secret.key", "control.db", "id_rsa", "id_ed25519")`, leaving `.env` completely readable by `read_file`.
* **Fix**:
  Add `.env`, `control.db`, `control.db-wal`, `control.db-shm` to `_PROTECTED_READ_NAMES`, and add `.env` and `control.db` to the hard-denied mutation rules.
* **Reproduction Steps**:
  1. Call `guard.guard_call("write_file", {"filename": "data/control.db", "content": "corrupted"})`.
  2. The guard check returns without raising `ToolDenied`.

---

## 2. Reliability Review

### REL-01: Empty or Short Process Name in `close_app` Triggers Mass System Termination
* **Severity**: Critical
* **Confidence**: High (100%)
* **Location**: [`assistant/system_tasks.py:L542-L580`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/system_tasks.py#L542-L580)
* **Description**:
  `close_app(app_name)` computes:
  ```python
  clean_name = str(app_name).lower().strip().replace("close ", "").replace("kill ", "")
  ```
  And matches processes via:
  ```python
  if p_name in target_exes or clean_name in p_name:
      proc.terminate()
  ```
  If `app_name` is `"close"` or `"kill"`, `clean_name` becomes the empty string `""`. Because `"" in p_name` evaluates to `True` for every running process, it iterates through all system processes on the computer and terminates them.
  Furthermore, if `clean_name` is short (e.g. `"s"`, `"win"`, `"host"`), it matches and terminates core Windows services (`svchost.exe`, `explorer.exe`, `sihost.exe`), causing Windows to crash or force-restart.
* **Fix**:
  Require `clean_name` to be at least 3 characters and not a generic substring. Reject empty strings immediately. Only allow substring matching on exact known process basenames.
* **Reproduction Steps**:
  1. In a Python session, execute `close_app("close")` or `close_app("s")`.
  2. Notice the termination loop kills running background processes and desktop applications indiscriminately.

---

### REL-02: Multi-Approval Premature Task Resumption in Control Plane Service
* **Severity**: High
* **Confidence**: High (100%)
* **Location**: [`assistant/control/service.py:L759-L764`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/control/service.py#L759-L764)
* **Description**:
  In `ControlPlane.resolve_approval(approval_id, approved)`:
  ```python
  if approval.task_id:
      task = self.store.get_task(approval.task_id)
      if task is not None and task.status == TaskStatus.WAITING_APPROVAL:
          self._set_task_status(
              task, TaskStatus.RUNNING if approved else TaskStatus.CANCELLED)
  ```
  If a task produces multiple approval requests (e.g. two sensitive capabilities requested during parallel plan execution), approving the first one immediately sets `task.status = TaskStatus.RUNNING`.
  In `TaskExecutor._wait_for_approvals()`:
  ```python
  if task.status != TaskStatus.WAITING_APPROVAL:
      return True
  ```
  Because the status was switched to `RUNNING` by the first approval resolution, `_wait_for_approvals()` unblocks and resumes execution even though the second approval remains pending in SQLite.
* **Fix**:
  Before switching task status back to `RUNNING`, verify whether any other approvals for the same `task_id` remain pending:
  ```python
  remaining = [a for a in self.store.list_approvals(pending_only=True)
               if a.task_id == approval.task_id and a.id != approval.id]
  if not remaining:
      self._set_task_status(task, TaskStatus.RUNNING)
  ```
* **Reproduction Steps**:
  1. Create a task with two pending approvals.
  2. Resolve the first approval.
  3. Inspect `task.status`: it transitions to `RUNNING` while approval #2 is still unresolved.

---

### REL-03: Unbounded Memory Growth in Rate Limiter In-Memory Token Bucket
* **Severity**: Medium
* **Confidence**: Medium-High (95%)
* **Location**: [`assistant/api/auth.py:L59, L88-L97`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/api/auth.py#L59)
* **Description**:
  In `TokenBucket`, when `self.store` is `None` or for transient caller identities, state is maintained in `self._tokens = {}`.
  Each new caller identity stores a tuple `(tokens, last_updated)`. There is no maximum dictionary capacity, LRU eviction, or TTL pruning. In long-running assistant sessions or under randomized client IPs/identifiers, `_tokens` expands indefinitely, leaking memory.
* **Fix**:
  Implement periodic TTL cleanup or cap `_tokens` with an `OrderedDict` LRU eviction strategy (e.g. max 1,000 active entries).
* **Reproduction Steps**:
  1. Run `limiter.take(str(uuid.uuid4()))` 100,000 times in a test.
  2. Observe `len(limiter._tokens)` reaches 100,000 and retains all stale entries indefinitely.

---

### REL-04: Windows Reserved Device Names and Second-Level Timestamp Collisions in `save_upload`
* **Severity**: Medium
* **Confidence**: High (100%)
* **Location**: [`assistant/files.py:L236-L255`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/files.py#L236-L255)
* **Description**:
  1. `save_upload` strips directory traversal via `Path(filename).name`, but does not sanitize Windows reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1-COM9`, `LPT1-LPT9`). On Windows NTFS, writing to `CON` redirects to the console, while writing to `COM1` or `AUX` can hang or crash the I/O thread.
  2. When `overwrite=False` and a file exists, collision avoidance appends `int(time.time())`:
     ```python
     destination = target_dir / f"{stem}-{int(time.time())}{suffix}"
     ```
     If multiple files are uploaded within the same second (e.g. batch mobile upload), the timestamp suffix is identical, causing silent overwrite or collision.
* **Fix**:
  Reject or rename reserved device names (`CON`, `NUL`, etc.) and NTFS alternate data streams (`:`). Use `uuid.uuid4().hex[:8]` or microsecond timestamps for collision suffixes.
* **Reproduction Steps**:
  1. Upload two files with the same name within 1 second with `overwrite=False`.
  2. The second upload collides with the first upload's generated name.

---

## 3. Performance Review

### PERF-01: Synchronous Outbound Telegram HTTP Requests Block Control Plane Timeline
* **Severity**: High
* **Confidence**: High (100%)
* **Location**: [`assistant/control/notifier.py:L82-L97, L165-L172`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/control/notifier.py#L82-L97)
* **Description**:
  The `Notifier` subscribes to control plane events via `plane.subscribe(self._on_event)`. In `ControlPlane.record()`, every subscriber callback is invoked synchronously on the thread that recorded the event (e.g. task execution worker, API request handler, approval resolver).
  In `TelegramChannel.deliver()`, it calls `send_telegram_message()`, which makes a blocking HTTP request using `urllib.request.urlopen` with a 15-second timeout.
  When Telegram has high latency or network connectivity drops, every single notified event freezes the calling thread for up to 15 seconds.
* **Fix**:
  Decouple notification delivery from the event subscriber by pushing notifications to an internal queue serviced by a dedicated background worker thread.
* **Reproduction Steps**:
  1. Set a valid Telegram bot token and simulate a slow/unreachable network connection.
  2. Call `plane.record("Test event")`.
  3. The `plane.record()` invocation blocks synchronously for 15 seconds before returning.

---

### PERF-02: Redundant SQLite Queries and Decryption on Every Timeline Event and Log Line
* **Severity**: Medium
* **Confidence**: Medium-High (95%)
* **Location**: [`assistant/control/secrets.py:L179-L197`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/control/secrets.py#L179-L197)
* **Description**:
  `SecretStore.redact(text)` is invoked on every event logged in the control plane to scrub credentials.
  In `redact()`:
  ```python
  for name in self.names():
      value = self.reveal(name, capability="*")
  ```
  `self.names()` executes `SELECT name FROM secrets`. Then for each secret name, `self.reveal()` executes another SQLite query (`SELECT * FROM secrets WHERE name = ?`) and performs Fernet symmetric decryption.
  If there are 10 secrets in the vault, writing one timeline event executes 11 SQL queries and 10 Fernet decryptions. Under typical multi-step execution, this generates hundreds of redundant SQL queries per minute.
* **Fix**:
  Cache plaintext secret values in an in-memory dictionary behind `self._lock`, invalidating the cache only when secrets are saved (`put()`) or deleted.
* **Reproduction Steps**:
  1. Insert 10 secrets into `SecretStore`.
  2. Log 50 activity events.
  3. Observe over 550 SQL queries and 500 Fernet decryptions executed solely for redaction.

---

### PERF-03: Indefinite Future Wait on Browser Thread Worker Causes Unrecoverable Task Hang
* **Severity**: Medium
* **Confidence**: Medium (90%)
* **Location**: [`assistant/browser/session.py:L86-L93`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/browser/session.py#L86-L93)
* **Description**:
  In `_BrowserThread.call(func, *args, **kwargs)`:
  ```python
  future = concurrent.futures.Future()
  self._queue.put((func, args, kwargs, future))
  return future.result()
  ```
  `future.result()` is called without a timeout parameter. If a Playwright call hangs inside the dedicated browser worker (e.g. unresponsive modal dialog, network deadlock, or unhandled browser crash), `future.result()` blocks the task execution thread indefinitely with no way to timeout or recover.
* **Fix**:
  Pass a timeout to `future.result(timeout=self.timeout_seconds)` and raise a descriptive `TimeoutError` or `BrowserUnavailable` exception if the worker does not respond.
* **Reproduction Steps**:
  1. Enqueue a task on `_BrowserThread` that waits on an unresolvable condition.
  2. Call `session.goto(...)`.
  3. The task worker thread remains blocked forever.

---

## 4. Architecture Review

### ARCH-01: Missing Client Device Attribution Stamps All Remote Tasks as Originating Locally
* **Severity**: Medium
* **Confidence**: Medium-High (95%)
* **Location**: [`assistant/api/app.py:L435-L451`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/api/app.py#L435-L451), [`assistant/control/service.py:L349-L356`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/control/service.py#L349-L356)
* **Description**:
  In `assistant/control/service.py`:
  ```python
  def create_task(self, goal, steps=None, capability=None):
      task = Task(goal=goal, status=TaskStatus.PENDING,
                  device_id=self.local_device.id)
  ```
  The API endpoint `POST /api/tasks` in `assistant/api/app.py` does not pass the calling authenticated device ID (`request.state.device.id`) to `plane.create_task()`.
  Consequently, all tasks created remotely via mobile clients, paired laptops, or external agents are attributed to `self.local_device.id`. This corrupts audit trails and prevents device-scoped policy rules from enforcing per-device security boundaries.
* **Fix**:
  Update `create_task()` to accept an optional `device_id` parameter (defaulting to `self.local_device.id`), and pass `device.id` from `request.state.device` in `app.py`.
* **Reproduction Steps**:
  1. Pair a remote device and obtain a token.
  2. Call `POST /api/tasks` with the Bearer token.
  3. Query `task.device_id` from the database. It reports the local host ID instead of the paired device ID.

---

### ARCH-02: Non-Atomic Persistence in `SiteMemory` JSON Storage Risks Data Corruption
* **Severity**: Medium
* **Confidence**: Medium (90%)
* **Location**: [`assistant/site_memory.py:L53-L59`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/site_memory.py#L53-L59)
* **Description**:
  In `SiteMemory._save(data)`:
  ```python
  self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
  ```
  `self.path.write_text()` opens the file directly in write mode, truncating it before writing the new JSON payload. If the application is terminated, loses power, or crashes mid-write, `data/site_notes.json` is corrupted and emptied.
  Other persistence modules (such as `assistant/config.py`) correctly utilize atomic write patterns with temporary files and `os.replace()`.
* **Fix**:
  Write new JSON to a temporary file (`site_notes.json.tmp`) in the same directory and atomically replace the destination file using `os.replace()`.
* **Reproduction Steps**:
  1. Simulate process termination or write failure during `SiteMemory.remember()`.
  2. Inspect `site_notes.json`: the file contains partial JSON or is 0 bytes.

---

## 5. Edge-Case Review

### EDGE-01: Overly Greedy Substring Match in `handle_volume_command` Intercepts Math & Science Questions
* **Severity**: High
* **Confidence**: High (100%)
* **Location**: [`assistant/commands.py:L165-L187`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/commands.py#L165-L187)
* **Description**:
  In `assistant/commands.py`:
  ```python
  if "volume" not in command and "mute" not in command:
      return False
  ...
  amount = extract_number(command)
  ...
  if amount is not None:
      set_volume(amount)
      return True
  ```
  If a user asks a general knowledge or math question containing the word "volume" and any number (e.g. "what is the volume of a sphere with radius 5?", "calculate volume of 50 boxes"), the command router extracts the number, changes the system volume to 5% or 50%, and terminates command routing with `True`. The question is never routed to the AI brain.
* **Fix**:
  Replace loose substring checks with targeted regex patterns (e.g. `r"^(?:set\s+)?volume\s+(?:to\s+)?(\d+)$"` or `r"^volume\s+(?:up|down)"`) so general language questions are not hijacked.
* **Reproduction Steps**:
  1. Run `python main.py --text "what is the volume of a sphere of radius 10" --no-speech`.
  2. The assistant sets the speaker volume to 10% instead of providing the mathematical answer.

---

### EDGE-02: Unanchored Phrase Match in `change_assistant_name` Renames Assistant During Natural Conversation
* **Severity**: Medium
* **Confidence**: High (100%)
* **Location**: [`assistant/commands.py:L74-L91`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/commands.py#L74-L91)
* **Description**:
  `change_assistant_name(command)` uses substring containment:
  ```python
  phrases = ["change assistant name to", "set assistant name to", "change your name to", "set your name to"]
  for phrase in phrases:
      if phrase in command:
          new_name = command.split(phrase, 1)[1].strip().title()
  ```
  If a user asks a conversational question like "Can I change your name to Jarvis tomorrow?", the substring matches, extracts `"Jarvis Tomorrow?"`, and renames the assistant in `config.json`.
* **Fix**:
  Use an anchored regex matching command intent at the beginning of the sentence:
  ```python
  _NAME_CHANGE_PATTERN = re.compile(
      r"^(?:please\s+)?(?:change|set)\s+(?:assistant\s+name|your\s+name)\s+to\s+(.+)$",
      re.IGNORECASE,
  )
  ```
* **Reproduction Steps**:
  1. Run `python main.py --text "can i change your name to friday later" --no-speech`.
  2. Inspect `config.json`: `assistant_name` is modified to `"Friday Later"`.

---

### EDGE-03: Subprocess Execution Lacks Stdin Disconnect, Causing 15s Hang on Interactive Commands
* **Severity**: Low
* **Confidence**: Medium-High (95%)
* **Location**: [`assistant/system_tasks.py:L1490-L1505`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/system_tasks.py#L1490-L1505)
* **Description**:
  In `run_terminal_command(command)`:
  ```python
  result = subprocess.run(command, shell=True, capture_output=True, timeout=15)
  ```
  `stdin` is not redirected to `subprocess.DEVNULL`. On Windows, commands that trigger console input prompts (such as `pause`, batch scripts prompting for confirmation `[Y/N]`, or git asking for credentials) wait on stdin until the 15-second timeout expires.
* **Fix**:
  Pass `stdin=subprocess.DEVNULL` to `subprocess.run()`.
* **Reproduction Steps**:
  1. Execute `run_terminal_command("pause")`.
  2. Observe the command hangs for the full 15 seconds before timing out.

---

## Foolproof Hardening Implementation Plan

To ensure the system is completely resilient and foolproof before adding new subsystems, the following phased hardening plan is structured:

1. **Phase 1: Critical Security Hardening (SEC-01 to SEC-04)**
   - Add origin validation for all state-changing endpoints in `assistant/api/app.py`.
   - Add origin check to all WebSocket connection handlers in `assistant/api/app.py`.
   - Populate `TOOL_CAPABILITIES` with all 16 missing tool mappings and make `_authorizer` reject unknown tools.
   - Update `assistant/guard.py` to block mutations and reads of `.env` and `control.db`.

2. **Phase 2: Reliability & Process Safety Hardening (REL-01 to REL-04)**
   - Harden `close_app` in `assistant/system_tasks.py` with minimum length checks and strict process name filtering.
   - Fix `resolve_approval` in `assistant/control/service.py` to check for remaining pending approvals before resuming task.
   - Add LRU pruning / TTL bounds to `TokenBucket._tokens` in `assistant/api/auth.py`.
   - Sanitize Windows device reserved names and microsecond collision suffixes in `assistant/files.py`.

3. **Phase 3: Performance & Resource Decoupling (PERF-01 to PERF-03)**
   - Dispatch `TelegramChannel.deliver` asynchronously via background executor in `assistant/control/notifier.py`.
   - Add in-memory plaintext cache for secrets in `assistant/control/secrets.py` to eliminate redundant SQL queries in `redact()`.
   - Add explicit timeout parameter to `_BrowserThread.call()` in `assistant/browser/session.py`.

4. **Phase 4: Architecture & Routing Edge Cases (ARCH-01, ARCH-02, EDGE-01 to EDGE-03)**
   - Propagate `request.state.device.id` to `plane.create_task()` in `assistant/api/app.py`.
   - Implement atomic temp-file replace in `assistant/site_memory.py`.
   - Replace greedy substring checks in `handle_volume_command` and `change_assistant_name` with strict regexes in `assistant/commands.py`.
   - Add `stdin=subprocess.DEVNULL` to `run_terminal_command` in `assistant/system_tasks.py`.
