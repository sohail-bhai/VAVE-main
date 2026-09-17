# VAVE Desktop Assistant & Control Plane: Comprehensive Error & Audit Report

**Date**: 2026-09-17  
**Scope**: `assistant/`, `gui/`, `config.json`, Core Architecture & Protocols  
**Total Findings**: 19 across 5 distinct review domains (Security, Reliability, Performance, Architecture, Edge-Case)

---

## Executive Summary Matrix

| ID | Domain | Severity | Confidence | Location | Summary |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **SEC-01** | Security | **Critical** (CVSS 9.8) | 100% | [`assistant/api/app.py:L269-L312`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/api/app.py#L269-L312) | Wildcard CORS + localhost trust enables cross-origin token minting & computer takeover |
| **SEC-02** | Security | **High** (CVSS 8.6) | 100% | [`assistant/web_api.py:L64-L165`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/web_api.py#L64-L165) | SSRF check bypass via HTTP 30x redirects & TOCTOU DNS rebinding |
| **SEC-03** | Security | **High** (CVSS 7.5) | 100% | [`assistant/files.py:L61-L246`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/files.py#L61-L246) | Sliced `_is_hidden()` check exposes subfolder dotfiles & `.git` secrets |
| **SEC-04** | Security | **Medium** (CVSS 6.5) | 95% | [`assistant/guard.py:L121-L136`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/guard.py#L121-L136) | Read tools bypass mutation guard, exposing `secret.key` and private keys |
| **REL-01** | Reliability | **Critical** | 100% | [`assistant/control/executor.py:L365-L384`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/control/executor.py#L365-L384) | Task steps mark finished immediately when capability approval requested |
| **REL-02** | Reliability | **High** | 100% | [`assistant/browser/session.py:L55-L119`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/browser/session.py#L55-L119) | Playwright synchronous thread affinity crash across thread pool workers |
| **REL-03** | Reliability | **High** | 95% | [`assistant/control/secrets.py:L239-L259`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/control/secrets.py#L239-L259) | Unclosed `ControlStore` instances cause SQLite database lock collisions on Windows |
| **REL-04** | Reliability | **Medium** | 100% | [`assistant/notes.py:L8-L26`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/notes.py#L8-L26) | Unhandled `FileNotFoundError` when `data/` directory does not exist |
| **PERF-01** | Performance | **High** | 100% | [`assistant/speech.py:L248-L258`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/speech.py#L248-L258) | Synchronous Telegram network request inside `speak()` stalls voice/UI loop |
| **PERF-02** | Performance | **High** | 100% | [`assistant/control/secrets.py:L249-L258`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/control/secrets.py#L249-L258) | 19 migration checks re-run on every single secret setting lookup |
| **PERF-03** | Performance | **Medium** | 95% | [`assistant/files.py:L209-L224`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/files.py#L209-L224) | Unindexed 20,000-file recursive directory traversal blocks event loop |
| **PERF-04** | Performance | **Medium** | 90% | [`assistant/ai_brain.py:L3177-L3184`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/ai_brain.py#L3177-L3184) | Massive tool schema payload re-evaluated every turn on small local model |
| **ARCH-01** | Architecture | **High** | 95% | [`assistant/control/executor.py:L357-L384`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/control/executor.py#L357-L384) | Split-brain authorization: Control plane grants conflict with guard tiers |
| **ARCH-02** | Architecture | **High** | 95% | [`assistant/call_context.py:L16-L60`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/call_context.py#L16-L60) | Reused thread pool workers leak ContextVar taint state across steps |
| **ARCH-03** | Architecture | **Medium** | 95% | [`assistant/config.py:L268-L280`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/config.py#L268-L280) | Mutating config cache in-place purges in-memory secrets and lacks atomic save |
| **EDGE-01** | Edge-Case | **High** | 100% | [`assistant/commands.py:L55-L66`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/commands.py#L55-L66) | Substring `"my name is"` in question overwrites user name in config |
| **EDGE-02** | Edge-Case | **Medium** | 95% | [`assistant/files.py:L260-L271`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/files.py#L260-L271) | `..` in destination filename moves file outside shared root |
| **EDGE-03** | Edge-Case | **Medium** | 90% | [`assistant/notes.py:L30-L42`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/notes.py#L30-L42) | TOCTOU double-read race condition in `read_notes` |
| **EDGE-04** | Edge-Case | **Low** | 95% | [`assistant/system_tasks.py:L1479-L1485`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/system_tasks.py#L1479-L1485) | Windows CMD non-UTF8 code page crashes on Unicode command output |

---

## 1. Security Review

### SEC-01: Cross-Origin Localhost Authentication Bypass & CSRF Token Minting
* **Severity**: Critical (CVSS 9.8 - AV:N/AC:L/PR:N/UI:R/S:C/C:H/I:H/A:H)
* **Confidence**: High (100%)
* **Location**: [`assistant/api/app.py:L269-L312`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/api/app.py#L269-L312), [`assistant/api/auth.py:L219-L251`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/api/auth.py#L219-L251)
* **Description**:
  The API binds with `CORSMiddleware` configured to `allow_origins=["*"]`. Simultaneously, `config.json` sets `api_trust_localhost: true` by default. In `assistant/api/auth.py`, `authenticate()` permits unauthenticated access if the client connects from loopback (`127.0.0.1`, `localhost`, `::1`). When a user visits any malicious website in Chrome or Edge, attacker JavaScript can make a cross-origin `POST http://127.0.0.1:8765/api/pair/code`. The browser connects from `127.0.0.1`, bypassing authentication. Because wildcard CORS is active, the browser does not block the response and exposes the generated 6-digit code to the attacker's script. The script immediately posts to `/api/pair` to obtain a permanent authenticated device token, gaining full remote command execution and local machine takeover.
* **Fix**:
  1. Remove `allow_origins=["*"]` on sensitive endpoints; restrict CORS to trusted origin domains.
  2. Validate the `Origin` and `Sec-Fetch-Site` headers for loopback requests. Reject `/api/pair/code` and administrative endpoints if `Origin` is present and untrusted.
  3. Require an explicit local file secret or pairing trigger rather than trusting raw client IP addresses.
* **Reproduction Steps**:
  1. Start the API server: `python -m assistant.api`.
  2. Open any browser to an external website (e.g., `https://example.com`).
  3. Open DevTools Console and execute:
     ```javascript
     fetch("http://127.0.0.1:8765/api/pair/code", { method: "POST" })
       .then(r => r.json())
       .then(data => console.log("Extracted code:", data.code));
     ```
  4. Observe that the browser receives and prints the pairing code due to `Access-Control-Allow-Origin: *`.

---

### SEC-02: SSRF Filter Bypass via HTTP 30x Redirects & TOCTOU DNS Rebinding
* **Severity**: High (CVSS 8.6 - AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:N/A:N)
* **Confidence**: High (100%)
* **Location**: [`assistant/web_api.py:L64-L98`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/web_api.py#L64-L98), [`assistant/web_api.py:L157-L165`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/web_api.py#L157-L165)
* **Description**:
  `check_address(url)` performs initial hostname resolution to detect private/loopback IP ranges. However, `urllib.request.urlopen(request)` is executed without a custom redirect handler. Python's default handler follows HTTP 301/302 redirects automatically without re-checking the destination IP. An attacker-controlled web service can respond with `302 Found` pointing to internal endpoints (`http://127.0.0.1:8765/api/status` or `http://169.254.169.254/latest/meta-data/`). Additionally, performing DNS lookup in `check_address` separate from the connection in `urlopen` creates a Time-of-Check to Time-of-Use (TOCTOU) DNS rebinding vulnerability.
* **Fix**:
  1. Subclass `urllib.request.HTTPRedirectHandler` and override `redirect_request`: validate every redirect destination through `check_address(new_url)` before following.
  2. Strip Authorization and custom secret headers if the redirect target has a different host or scheme.
* **Reproduction Steps**:
  1. Start a local server on port 8765.
  2. Run an external public HTTP mock server that redirects:
     `Location: http://127.0.0.1:8765/api/status`.
  3. Execute `assistant.web_api.call("GET", "http://external-mock.com/redirect")`.
  4. Observe that the internal status response is returned to the caller.

---

### SEC-03: Sliced `_is_hidden()` Check Exposes Subfolder Dotfiles & `.git` Secrets
* **Severity**: High (CVSS 7.5 - AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:N/A:N)
* **Confidence**: High (100%)
* **Location**: [`assistant/files.py:L61-L66`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/files.py#L61-L66), [`assistant/files.py:L227-L246`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/files.py#L227-L246)
* **Description**:
  In `assistant/files.py`, `_is_hidden(path)` checks only the final segment:
  ```python
  return any(part.startswith(".") and part not in (".", "..") for part in path.parts[-1:])
  ```
  If a path is `share_name/.git/config` or `share_name/.secrets/db.json`, `path.parts[-1:]` is `("config",)` or `("db.json",)`. Because `"config"` does not start with `.`, `_is_hidden` returns `False`. Clients can download any hidden file located inside a subdirectory. Furthermore, `save_upload` does not call `_is_hidden(safe_name)`, allowing clients to overwrite `.env` or `control.db` inside shared folders.
* **Fix**:
  1. Check all segments in `_is_hidden`:
     ```python
     def _is_hidden(path):
         parts = set(path.parts)
         if any(name in parts for name in HIDDEN_NAMES):
             return True
         return any(part.startswith(".") and part not in (".", "..") for part in path.parts)
     ```
  2. Enforce `_is_hidden(safe_name)` in `save_upload()`.
* **Reproduction Steps**:
  1. In a shared folder, create a directory `.git` containing a file `config`.
  2. Call `assistant.files.open_for_download("share_name/.git/config")`.
  3. Observe that the file is resolved and downloaded without a `FileAccessError`.

---

### SEC-04: Credential and Private Key Exposure via Unbounded File Reading
* **Severity**: Medium (CVSS 6.5 - AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:N/A:N)
* **Confidence**: High (95%)
* **Location**: [`assistant/guard.py:L121-L136`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/guard.py#L121-L136), [`assistant/system_tasks.py:L1500-L1510`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/system_tasks.py#L1500-L1510)
* **Description**:
  `_is_hard_denied()` in `guard.py` only protects files against mutation tools (`write_file`, `delete_file`, etc.). Reading files via `read_file(path)` has no workspace path containment checks. An LLM agent or caller can execute `read_file("C:/Users/<user>/.ssh/id_rsa")` or `read_file("data/control.db")`. In addition, `run_terminal_command("type data\\secret.key")` is not blocked by `_DESTRUCTIVE_COMMAND_REGEX` (which only checks deletion keywords), allowing secret keys to be exfiltrated.
* **Fix**:
  1. Constrain `read_file` to paths within project directories or configured shares.
  2. Add an explicit check in `_is_hard_denied` preventing read access to `secret.key`, `control.db`, and user `.ssh`/`.aws` files.
* **Reproduction Steps**:
  1. Call `assistant.system_tasks.read_file(str(Path.home() / ".ssh" / "id_rsa"))`.
  2. The call succeeds and returns the private key contents directly.

---

## 2. Reliability Review

### REL-01: Premature Step Completion and Drop of Pending Capability Approvals
* **Severity**: Critical
* **Confidence**: High (100%)
* **Location**: [`assistant/control/executor.py:L365-L384`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/control/executor.py#L365-L384), [`assistant/ai_brain.py:L2763-L2773`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/ai_brain.py#L2763-L2773), [`assistant/control/service.py:L732-L736`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/control/service.py#L732-L736)
* **Description**:
  When a step attempts to execute a tool requiring approval, `_authorizer()` calls `plane.request_capability()`, which transitions the task to `TaskStatus.WAITING_APPROVAL`. However, `_authorizer()` returns `(False, "needs your approval first...")` to `_agent_loop`. The model receives the tool refusal, outputs a message ("I need your approval to run this"), and ends its turn. `_call_runner()` returns the output text to `_run_step()`, which calls `plane.finish_step(task_id, step.position, detail=result.output)`. The step is marked `finished` in SQLite. When the user approves the pending approval in the GUI or mobile app, the step has already terminated without executing the tool.
* **Fix**:
  1. In `_authorizer()`, if capability status is `"waiting"`, do not return refusal to the model loop. Block on an approval resolution condition variable or event until resolved or timed out.
  2. Alternatively, raise a dedicated `StepWaitingApproval` exception in `_run_step()` to pause the step and leave it in `running`/`waiting_approval` status until explicitly resumed.
* **Reproduction Steps**:
  1. Create a task whose first step runs a command requiring approval (e.g. `run_terminal_command`).
  2. The step initiates, requests approval, and returns.
  3. Inspect SQLite database `task_steps`: the step status is already `finished`.
  4. Approving the request via `POST /api/approvals/{id}/resolve` has no effect.

---

### REL-02: Playwright Thread Affinity Violation in Concurrent Step Executor
* **Severity**: High
* **Confidence**: High (100%)
* **Location**: [`assistant/browser/session.py:L55-L119`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/browser/session.py#L55-L119), [`assistant/control/executor.py:L203-L225`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/control/executor.py#L203-L225)
* **Description**:
  `BrowserSession` initializes Playwright's synchronous API (`sync_playwright().start()`) on the thread that first calls `start()`. Playwright's sync API strictly mandates that all calls originate from the thread where it was instantiated. `TaskExecutor._run_graph()` dispatches task steps concurrently via `ThreadPoolExecutor(max_workers=3)`. If Step 1 opens the browser on thread worker A, and Step 2 calls `browse` or `browser_click` on thread worker B, Playwright raises `Error: Playwright Sync API must be called from the thread it was created on`.
* **Fix**:
  1. Run all Playwright browser interactions on a dedicated single-threaded event loop or queue worker thread.
  2. Relay browser actions across threads via a thread-safe request/response queue.
* **Reproduction Steps**:
  1. Start the browser in a worker thread:
     ```python
     import threading
     from assistant.browser.session import get_session
     session = get_session()
     t = threading.Thread(target=session.start)
     t.start()
     t.join()
     ```
  2. From the main thread, execute `session.goto("https://example.com")`.
  3. Playwright immediately crashes with a thread affinity violation error.

---

### REL-03: Concurrent SQLite Connection Spawning and Database Locking on Windows
* **Severity**: High
* **Confidence**: High (95%)
* **Location**: [`assistant/control/secrets.py:L239-L259`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/control/secrets.py#L239-L259), [`assistant/control/store.py:L279-L297`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/control/store.py#L279-L297)
* **Description**:
  `assistant/control/secrets.py:resolve_setting(value)` instantiates a new `ControlStore()` on every lookup without closing it:
  ```python
  store = SecretStore(ControlStore(), key=load_key())
  return store.resolve_setting(value, default=default)
  ```
  `resolve_setting()` is invoked during speech output, notifications, telegram sync, and system tasks. Each call opens a SQLite connection and executes schema migrations. On Windows, unclosed file handles and active WAL locks cause `sqlite3.OperationalError: database is locked`.
* **Fix**:
  1. Reuse the existing singleton `ControlStore` instance from `get_control_plane().store`.
  2. Implement proper context management (`with ControlStore() as store:`) if independent instances are ever required.
* **Reproduction Steps**:
  1. Spawn 20 concurrent threads calling `resolve_setting("secret://telegram_bot_token")`.
  2. On Windows, observe file handle exhaustion and intermittent `sqlite3.OperationalError: database is locked`.

---

### REL-04: Unhandled FileNotFoundError in Notes Storage Initialization
* **Severity**: Medium
* **Confidence**: High (100%)
* **Location**: [`assistant/notes.py:L8-L26`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/notes.py#L8-L26)
* **Description**:
  `NOTES_FILE = PROJECT_ROOT / "data" / "notes.txt"`. In `add_note()`, the file is appended via `open(NOTES_FILE, "a", encoding="utf-8")`. If `data/` does not already exist on a fresh clone or test run, `open()` raises `FileNotFoundError`.
* **Fix**:
  Ensure parent directory existence:
  ```python
  NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
  ```
* **Reproduction Steps**:
  1. Delete the `data/` directory.
  2. Invoke `assistant.notes.add_note()`.
  3. Observe unhandled `FileNotFoundError: [Errno 2] No such file or directory: '.../data/notes.txt'`.

---

## 3. Performance Review

### PERF-01: Blocking Outbound HTTP Request in Synchronous Speech Engine
* **Severity**: High
* **Confidence**: High (100%)
* **Location**: [`assistant/speech.py:L248-L258`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/speech.py#L248-L258), [`assistant/telegram_sync.py:L174-L200`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/telegram_sync.py#L174-L200)
* **Description**:
  In `assistant/speech.py`, when `call_context.get_origin() == "telegram"`, `speak(text)` synchronously invokes `send_telegram_message(tok, cid, text)`. This issues a blocking HTTPS request via `urllib.request.urlopen` with a 15-second timeout on the caller's thread. If the Telegram API is slow or offline, the entire UI dashboard or voice loop freezes.
* **Fix**:
  Dispatch Telegram forwarding to a background worker thread or queue:
  ```python
  threading.Thread(target=send_telegram_message, args=(tok, cid, text), daemon=True).start()
  ```
* **Reproduction Steps**:
  1. Set `call_context.set_origin("telegram")`.
  2. Simulate network latency to `api.telegram.org`.
  3. Call `speak("Done")`. Observe caller thread blocking for the duration of the network request.

---

### PERF-02: Repeated Migration Execution and Database Reopening During Secret Lookups
* **Severity**: High
* **Confidence**: High (100%)
* **Location**: [`assistant/control/secrets.py:L249-L258`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/control/secrets.py#L249-L258), [`assistant/control/store.py:L288-L320`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/control/store.py#L288-L320)
* **Description**:
  Every call to `resolve_setting()` instantiates `ControlStore()`. In `ControlStore.__init__`, SQLite opens, sets PRAGMA statements, and runs `_migrate()`. `_migrate()` evaluates 19 migrations, performing multiple queries against `sqlite_master` on every single secret lookup.
* **Fix**:
  Cache the `SecretStore` instance globally and execute migrations only once at application bootstrap.
* **Reproduction Steps**:
  1. Time the execution of `[resolve_setting("plain_text") for _ in range(50)]`.
  2. Observe several seconds consumed purely by SQLite connection handshakes and table existence queries.

---

### PERF-03: Blocking Recursive Disk Crawl in File Search API
* **Severity**: Medium
* **Confidence**: High (95%)
* **Location**: [`assistant/files.py:L209-L224`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/files.py#L209-L224)
* **Description**:
  `search(query, path="", limit=200)` executes a synchronous `os.walk()` traversing up to `SEARCH_MAX_VISITED = 20_000` entries, calling `path.stat()` on every match. In directories containing `node_modules` or `.git`, this blocks the FastAPI event loop for several seconds.
* **Fix**:
  1. Filter out ignored directories (`node_modules`, `.git`, `venv`, `__pycache__`) during `os.walk`.
  2. Run the search inside an asynchronous thread executor (`await asyncio.to_thread(...)`).
* **Reproduction Steps**:
  1. Add a directory with a large `node_modules` folder to `file_shares`.
  2. Call `GET /api/files/search?query=index`.
  3. Observe the API worker freezing while scanning 20,000 files.

---

### PERF-04: Full Registry Schema Transmission on Every Local LLM Turn
* **Severity**: Medium
* **Confidence**: High (90%)
* **Location**: [`assistant/ai_brain.py:L3177-L3184`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/ai_brain.py#L3177-L3184), [`assistant/ai_brain.py:L80-L100`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/ai_brain.py#L80-L100)
* **Description**:
  `select_tools()` matches tools using broad regular expressions over all 57 registered functions. A large JSON schema payload (often exceeding 4,000 tokens) is transmitted to Ollama on every conversation turn. On 3B/4B local models, evaluating this prompt context introduces significant inference latency.
* **Fix**:
  Tighten tool selection criteria to cap tool schemas at 8-10 relevant tools per step.
* **Reproduction Steps**:
  1. Send a multi-word prompt to `run_task_step`.
  2. Check Ollama API logs: prompt evaluation tokens exceed 4,000 tokens before user instruction text begins.

---

## 4. Architecture Review

### ARCH-01: Dual Split-Brain Authorization Between Control Plane and Guard
* **Severity**: High
* **Confidence**: High (95%)
* **Location**: [`assistant/control/executor.py:L357-L384`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/control/executor.py#L357-L384), [`assistant/guard.py:L145-L201`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/guard.py#L145-L201), [`assistant/control/policy.py:L1-L80`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/control/policy.py#L1-L80)
* **Description**:
  Two distinct authorization engines operate concurrently without synchronization:
  1. `ControlPlane.policy`: database-backed, managing fine-grained capabilities (`system.command`, `file.read`).
  2. `assistant.guard`: configuration-backed, managing coarse risk tiers (`safe`, `sensitive`, `destructive`) per origin.
  During execution, `executor.py` checks capabilities and grants access. When `_agent_loop` calls `guard.call()`, `guard.py` independently evaluates `config.json`. If `config.json` denies destructive tools, `guard.call()` raises `ToolDenied` even though the control plane explicitly granted permission.
* **Fix**:
  Unify access control: make `guard.py` delegate permission checks to `ControlPlane.policy`, maintaining a single source of truth.
* **Reproduction Steps**:
  1. Grant capability `system.command` in the control plane.
  2. Set `config.json` `safety.voice.allow_destructive: false`.
  3. Execute task: control plane authorizes execution, but `guard.call()` aborts with `ToolDenied`.

---

### ARCH-02: ContextVar Pollution Across ThreadPoolExecutor Workers
* **Severity**: High
* **Confidence**: High (95%)
* **Location**: [`assistant/call_context.py:L16-L60`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/call_context.py#L16-L60), [`assistant/control/executor.py:L103-L108`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/control/executor.py#L103-L108), [`assistant/control/executor.py:L203-L225`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/control/executor.py#L203-L225)
* **Issue**:
  `call_context` relies on `contextvars.ContextVar` (`_call_origin`, `_untrusted_source`). `TaskExecutor` executes steps on pooled worker threads from `ThreadPoolExecutor`. In Python, thread pool workers persist across tasks. When Step 1 marks a worker thread as tainted (`mark_tainted`), that worker thread retains the taint. Subsequent clean steps scheduled on that same worker run with `is_tainted() == True`, prompting unnecessary confirmations for safe actions.
* **Fix**:
  Wrap step execution in `contextvars.copy_context().run` and ensure `clear_taint()` is executed in a `finally` block on each step.
* **Reproduction Steps**:
  1. Run a step on a thread pool worker that triggers `mark_tainted("web")`.
  2. Run a clean local step that executes on the same worker.
  3. `is_tainted()` returns `True`, incorrectly triggering taint confirmation.

---

### ARCH-03: In-Memory Cache Mutation and Non-Atomic Config Persistence
* **Severity**: Medium
* **Confidence**: High (95%)
* **Location**: [`assistant/config.py:L268-L280`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/config.py#L268-L280)
* **Issue**:
  In `update_setting(key, value)`:
  ```python
  config = load_config()
  config[key] = value
  for secret in SECRET_KEYS:
      config.pop(secret, None)
  save_config(config)
  ```
  `load_config()` returns the global `_config_cache` dictionary. Mutating `config` mutates `_config_cache` directly. Popping `SECRET_KEYS` destroys cached secrets in memory. Additionally, `save_config` writes directly to `config.json` without file locks or atomic rename (`tempfile` + `os.replace`), risking file corruption during concurrent writes.
* **Fix**:
  1. Deep-copy the config before modifying.
  2. Save via atomic file replacement (`tempfile` + `os.replace`).
  3. Protect configuration reads and writes with a `threading.Lock`.
* **Reproduction Steps**:
  1. Store credentials in `config.local.json`.
  2. Call `update_setting("user_name", "Test")`.
  3. Inspect `_config_cache`: all `SECRET_KEYS` are missing from memory.

---

## 5. Edge-Case Review

### EDGE-01: Voice Name Mutation Triggered by Substring in General Questions
* **Severity**: High
* **Confidence**: High (100%)
* **Location**: [`assistant/commands.py:L55-L66`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/commands.py#L55-L66)
* **Issue**:
  `change_user_name()` checks `if phrase in command:` with phrase `"my name is"`. If a user asks "Why is my name is on this invoice?" or "Do you know what my name is?", the parser splits on `"my name is"`:
  ```python
  new_name = command.split("my name is", 1)[1].strip().title()
  ```
  `user_name` in `config.json` is overwritten with `"On This Invoice"`, and the assistant announces "Okay, I will call you On This Invoice."
* **Fix**:
  Anchor the pattern to the beginning of the command:
  ```python
  match = re.match(r"^(?:change\s+my\s+name\s+to|set\s+my\s+name\s+to|my\s+name\s+is)\s+(.+)$", command)
  ```
* **Reproduction Steps**:
  1. Execute `assistant.commands.execute_command("explain why my name is alex")`.
  2. The parser updates `user_name` to `"Alex"` and halts command execution.

---

### EDGE-02: Directory Traversal via Dot-Dot Segment in File Relocation
* **Severity**: Medium
* **Confidence**: High (95%)
* **Location**: [`assistant/files.py:L260-L271`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/files.py#L260-L271)
* **Issue**:
  In `move(source, destination)`:
  ```python
  from_path = resolve(source)
  to_parent = resolve(str(Path(destination).parent))
  to_path = to_parent / Path(str(destination)).name
  ```
  If `destination` is `"folder/.."`, `Path("folder/..").name` evaluates to `..`. `to_path` resolves to `to_parent / ".."`. `shutil.move` moves `from_path` into the parent of `to_parent`. If `to_parent` is the share root, the file escapes the share boundaries.
* **Fix**:
  Validate that `Path(str(destination)).name not in (".", "..")` and verify `to_path.resolve()` remains inside the shared root.
* **Reproduction Steps**:
  1. Place `test.txt` in a shared folder.
  2. Call `assistant.files.move("test.txt", "subfolder/..")`.
  3. Observe the file moving outside the intended destination directory.

---

### EDGE-03: TOCTOU Race Condition and Double-Read in Notes Reader
* **Severity**: Medium
* **Confidence**: High (90%)
* **Location**: [`assistant/notes.py:L30-L42`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/notes.py#L30-L42)
* **Issue**:
  `read_notes()` calls `NOTES_FILE.read_text()` to check if the file is empty, then opens it again with `open(NOTES_FILE, "r")`. If another thread deletes or clears the file between checks, `open()` raises `FileNotFoundError`.
* **Fix**:
  Open the file once and handle errors cleanly:
  ```python
  try:
      with open(NOTES_FILE, "r", encoding="utf-8") as file:
          notes = [line.strip() for line in file if line.strip()]
  except FileNotFoundError:
      notes = []
  ```
* **Reproduction Steps**:
  1. Call `read_notes()` concurrently with `clear_notes()`.
  2. Observe intermittent `FileNotFoundError` exceptions.

---

### EDGE-04: Encoding Crash on Windows Console Output in Command Runner
* **Severity**: Low
* **Confidence**: High (95%)
* **Location**: [`assistant/system_tasks.py:L1479-L1485`](file:///f:/sohail/jarvis/JarvisAssistant_V1_2_Config/assistant/system_tasks.py#L1479-L1485)
* **Issue**:
  In `run_terminal_command`, `subprocess.run(command, text=True, ...)` relies on the Windows system code page (cp1252/cp437). When a command outputs non-ASCII or UTF-8 characters (e.g., git commits with emojis), `subprocess.run` raises `UnicodeDecodeError`.
* **Fix**:
  Pass `encoding="utf-8", errors="replace"` explicitly to `subprocess.run()`.
* **Reproduction Steps**:
  1. Run `run_terminal_command("python -c \"import sys; sys.stdout.buffer.write(b'\\xf0\\x9f\\x9a\\x80')\"")`.
  2. Observe `UnicodeDecodeError` in Windows console environments.
