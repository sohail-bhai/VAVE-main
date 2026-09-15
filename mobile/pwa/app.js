/* VAVE mobile client. No frameworks: fetch + EventSource + localStorage. */
"use strict";

const store = {
  get host() { return (localStorage.getItem("vave_host") || "").replace(/\/+$/, ""); },
  get token() { return localStorage.getItem("vave_token") || ""; },
  get name() { return localStorage.getItem("vave_name") || ""; },
  save(host, token, name) {
    localStorage.setItem("vave_host", host);
    localStorage.setItem("vave_token", token);
    if (name) localStorage.setItem("vave_name", name);
  },
  clear() {
    localStorage.removeItem("vave_host");
    localStorage.removeItem("vave_token");
    localStorage.removeItem("vave_name");
  }
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const res = await fetch(store.host + path, {
    ...options,
    headers: { "Authorization": "Bearer " + store.token, "Content-Type": "application/json", ...(options.headers || {}) }
  });
  if (res.status === 401 || res.status === 403) {
    // Token died or was revoked on the desktop.
    logout();
    throw new Error("Session ended. Please sign in again.");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || ("Request failed (" + res.status + ")"));
  }
  return res.json();
}

const esc = (s) => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;")
  .replace(/>/g, "&gt;").replace(/"/g, "&quot;");

function setConn(on, text) {
  $("conn").classList.toggle("on", !!on);
  $("conn-text").textContent = text || (on ? "Connected" : "Offline");
}

function setBadge(id, count) {
  const el = $(id);
  if (count > 0) { el.textContent = count > 99 ? "99+" : count; el.hidden = false; }
  else { el.hidden = true; }
}

function showError(where, message) {
  const el = $(where);
  el.textContent = message;
  el.hidden = false;
}

/* ---------------- login ---------------- */

function showLogin() {
  $("view-login").hidden = false;
  $("view-app").hidden = true;
  setConn(false);
}

function showApp() {
  $("view-login").hidden = true;
  $("view-app").hidden = false;
  setConn(true);
  refreshAll();
}

function logout() {
  stopStream();
  store.clear();
  showLogin();
}

async function boot() {
  $("login-host").value = store.host;
  $("login-name").value = store.name || "My phone";
  if (store.host && store.token) {
    try {
      const status = await api("/api/status");
      $("device-line").textContent = store.name ? ("Signed in as " + store.name) : "";
      showApp();
      return;
    } catch (e) { /* fall through to login screen */ }
  }
  showLogin();
}

/* ---------------- tasks ---------------- */

const STATUS_LABEL = {
  pending: "Pending", running: "Running", active: "Running",
  waiting_approval: "Waiting", completed: "Done", done: "Done",
  failed: "Failed", cancelled: "Cancelled", skipped: "Skipped"
};

function pill(status) {
  const s = String(status || "pending").toLowerCase();
  const cls = ["pending", "running", "active", "waiting_approval",
               "completed", "done", "failed", "cancelled", "skipped"].includes(s) ? s : "pending";
  return `<span class="pill ${cls}">${esc(STATUS_LABEL[s] || s)}</span>`;
}

function stepMark(step) {
  const s = String(step.status || "pending").toLowerCase();
  if (s === "done" || s === "completed") return "&#10003;";
  if (s === "failed" || s === "cancelled") return "&#10005;";
  if (s === "active" || s === "running") return "&#9654;";
  return "&#9675;";
}

function renderTask(task) {
  const steps = task.steps || [];
  const done = steps.filter((s) => ["done", "completed", "failed", "cancelled", "skipped"].includes(String(s.status || "").toLowerCase())).length;
  const pct = steps.length ? Math.round((done / steps.length) * 100) : 0;
  const rows = steps.map((s) =>
    `<div class="step ${esc(String(s.status || "").toLowerCase())}">
       <span class="mark">${stepMark(s)}</span>
       <div><div>${esc(s.label || s.title || "Step")}</div>
       ${s.error ? `<div class="note">${esc(s.error)}</div>` : ""}</div>
     </div>`).join("");
  return `<div class="card">
    <div class="task-head"><span class="task-goal">${esc(task.goal)}</span>${pill(task.status)}</div>
    ${task.output ? `<p class="muted">${esc(task.output)}</p>` : ""}
    ${rows}
    ${steps.length ? `<div class="progress"><div style="width:${pct}%"></div></div>` : ""}
  </div>`;
}

let openTaskId = null;

async function refreshTasks() {
  const tasks = await api("/api/tasks?limit=20");
  const list = $("task-list");
  if (!tasks.length) { list.innerHTML = `<div class="empty">Nothing yet. Send your first goal above.</div>`; return; }
  list.innerHTML = tasks.map(renderTask).join("");
}

async function submitGoal(ev) {
  ev.preventDefault();
  const input = $("goal-input");
  const goal = input.value.trim();
  if (!goal) return;
  input.value = "";
  input.disabled = true;
  try {
    const task = await api("/api/tasks", {
      method: "POST",
      body: JSON.stringify({ goal, autoplan: true, run: true })
    });
    openTaskId = task.id;
    await refreshTasks();
  } catch (e) {
    showError("login-error", ""); // reuse nothing; surface simply
    alert("Could not send: " + e.message);
  } finally {
    input.disabled = false;
    input.focus();
  }
}

/* ---------------- approvals ---------------- */

function renderApproval(a) {
  const decided = a.status && !["pending", "open", "waiting"].includes(String(a.status).toLowerCase());
  return `<div class="card" data-approval="${esc(a.id)}">
    <div class="approval-title">${esc(a.title || a.summary || "Approval needed")}</div>
    <div class="approval-desc">${esc(a.description || a.detail || "")}</div>
    <div class="approval-meta">Capability: ${esc(a.capability || "—")}${a.task_id ? " · task " + esc(a.task_id) : ""}</div>
    ${decided
      ? `<div class="approval-meta">Decided: ${esc(a.status)}</div>`
      : `<div class="approval-btns">
           <button class="deny" data-decide="false">Deny</button>
           <button class="approve" data-decide="true" data-needs-confirm="1">Approve</button>
         </div>`}
  </div>`;
}

async function refreshApprovals() {
  const approvals = await api("/api/approvals?pending_only=true");
  setBadge("badge-approvals", approvals.length);
  const list = $("approval-list");
  list.innerHTML = approvals.length
    ? approvals.map(renderApproval).join("")
    : `<div class="empty">Nothing waiting. Consequential actions will ask here first.</div>`;
}

async function decideApproval(id, approved, btn) {
  if (approved && btn.dataset.needsConfirm === "1") {
    // Second tap confirms a potentially destructive choice.
    btn.dataset.needsConfirm = "2";
    btn.textContent = "Tap again to confirm";
    return;
  }
  btn.disabled = true;
  try {
    await api("/api/approvals/" + encodeURIComponent(id), {
      method: "POST",
      body: JSON.stringify({ approved })
    });
    await refreshApprovals();
  } catch (e) {
    alert("Could not decide: " + e.message);
    btn.disabled = false;
  }
}

/* ---------------- notifications ---------------- */

function timeAgo(ts) {
  if (!ts) return "";
  const sec = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (sec < 60) return "just now";
  if (sec < 3600) return Math.floor(sec / 60) + "m ago";
  if (sec < 86400) return Math.floor(sec / 3600) + "h ago";
  return Math.floor(sec / 86400) + "d ago";
}

function renderAlert(n) {
  const ts = n.created_at || n.ts || n.time || 0;
  return `<div class="card alert ${n.unread === false || n.read ? "" : "unread"}" data-alert="${esc(n.id)}">
    <div class="alert-title">${esc(n.title || n.kind || "VAVE")}</div>
    <div class="alert-body">${esc(n.message || n.body || n.text || "")}</div>
    <div class="alert-time">${esc(timeAgo(ts))}${n.unread === false || n.read ? "" : " · unread"}</div>
  </div>`;
}

let unreadCount = 0;

async function refreshAlerts() {
  const items = await api("/api/notifications?limit=30");
  const list = Array.isArray(items) ? items : (items.notifications || items.items || []);
  unreadCount = list.filter((n) => !(n.unread === false || n.read)).length;
  setBadge("badge-alerts", unreadCount);
  $("alert-list").innerHTML = list.length
    ? list.map(renderAlert).join("")
    : `<div class="empty">Quiet for now.</div>`;
}

async function markAllRead() {
  const cards = document.querySelectorAll("#alert-list .card.alert.unread");
  for (const card of cards) {
    try {
      await api("/api/notifications/" + encodeURIComponent(card.dataset.alert) + "/read", { method: "POST" });
    } catch (e) { break; }
  }
  await refreshAlerts();
}

/* ---------------- live stream ---------------- */

let eventSource = null;
let streamFails = 0;

function stopStream() {
  if (eventSource) { eventSource.close(); eventSource = null; }
}

function startStream() {
  stopStream();
  if (!store.host || !store.token) return;
  const url = store.host + "/api/events/stream?token=" + encodeURIComponent(store.token);
  eventSource = new EventSource(url);
  eventSource.onopen = () => { streamFails = 0; setConn(true); };
  eventSource.onerror = () => {
    eventSource.close(); eventSource = null;
    setConn(false, "Reconnecting…");
    streamFails += 1;
    // Reconnect with backoff, capped at 30s. EventSource would retry
    // forever on its own; explicit backoff is kinder to the server.
    setTimeout(startStream, Math.min(30000, 2000 * Math.pow(2, Math.min(streamFails, 4))));
  };
  const refresh = () => refreshVisible();
  eventSource.addEventListener("activity", refresh);
  eventSource.addEventListener("connected", () => setConn(true));
}

function refreshVisible() {
  if ($("view-app").hidden) return;
  if (!$("tab-tasks").hidden) refreshTasks().catch(() => {});
  if (!$("tab-approvals").hidden) refreshApprovals().catch(() => {});
  if (!$("tab-alerts").hidden) refreshAlerts().catch(() => {});
}

let pollTimer = null;

function refreshAll() {
  refreshTasks().catch(() => {});
  refreshApprovals().catch(() => {});
  refreshAlerts().catch(() => {});
  startStream();
  if (pollTimer) clearInterval(pollTimer);
  // SSE is the live path; polling is the safety net while the app is open.
  pollTimer = setInterval(refreshVisible, 15000);
}

/* ---------------- wiring ---------------- */

function switchTab(name) {
  document.querySelectorAll(".tabs .tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $("tab-tasks").hidden = name !== "tasks";
  $("tab-approvals").hidden = name !== "approvals";
  $("tab-alerts").hidden = name !== "alerts";
  refreshVisible();
}

document.addEventListener("DOMContentLoaded", () => {
  $("tab-token").addEventListener("click", () => {
    $("tab-token").classList.add("active"); $("tab-pair").classList.remove("active");
    $("pane-token").hidden = false; $("pane-pair").hidden = true;
  });
  $("tab-pair").addEventListener("click", () => {
    $("tab-pair").classList.add("active"); $("tab-token").classList.remove("active");
    $("pane-pair").hidden = false; $("pane-token").hidden = true;
  });

  $("btn-token").addEventListener("click", async () => {
    const host = $("login-host").value.trim().replace(/\/+$/, "");
    const token = $("login-token").value.trim();
    if (!host || !token) { showError("login-error", "Host and token are both needed."); return; }
    store.save(host, token, $("login-name").value.trim() || "My phone");
    try {
      await api("/api/status");
      $("device-line").textContent = "Signed in as " + store.name;
      $("login-error").hidden = true;
      showApp();
    } catch (e) { showError("login-error", "Could not connect: " + e.message); }
  });

  $("btn-pair").addEventListener("click", async () => {
    const host = $("login-host").value.trim().replace(/\/+$/, "");
    const code = $("login-code").value.trim();
    const name = $("login-name").value.trim() || "My phone";
    if (!host || !code) { showError("login-error", "Host and pairing code are both needed."); return; }
    try {
      const res = await fetch(host + "/api/pair", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, name, kind: "phone" })
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || "Pairing failed.");
      store.save(host, body.token, name);
      $("device-line").textContent = "Signed in as " + store.name;
      $("login-error").hidden = true;
      showApp();
    } catch (e) { showError("login-error", "Could not pair: " + e.message); }
  });

  document.querySelectorAll(".tabs .tab").forEach((t) =>
    t.addEventListener("click", () => switchTab(t.dataset.tab)));

  $("goal-form").addEventListener("submit", submitGoal);

  $("approval-list").addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-decide]");
    if (!btn) return;
    const card = ev.target.closest("[data-approval]");
    if (!card) return;
    decideApproval(card.dataset.approval, btn.dataset.decide === "true", btn);
  });

  $("btn-read-all").addEventListener("click", markAllRead);
  $("btn-logout").addEventListener("click", logout);

  boot();

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
});
