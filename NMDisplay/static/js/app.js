/**
 * app.js — Boot, poll loop, draggable dividers, settings modal,
 *           session management, listener control, packet log panel.
 *
 * Owns all fetch() calls. Dispatches CustomEvents to map.js,
 * telemetry.js, timeline.js, and gauges.js — those modules never
 * call the API directly.
 */

"use strict";

let _cfg = {};
let _pollTimer  = null;
let _logTimer   = null;
let _lastPollOk = null;
let _logVisible = false;
let _browserPos = { lat: null, lon: null };   // updated by geolocation watchPosition

// ── Boot ────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", async () => {
  await boot();
});

async function boot() {
  _cfg = await fetchSettings();
  applyTheme(_cfg);

  document.dispatchEvent(new CustomEvent("nm:settings-loaded", { detail: { cfg: _cfg } }));

  _initGeolocation();

  NMMap.init("nm-map");
  NMTelemetry.init();
  NMTimeline.init("nm-timeline-canvas");
  NMGauges.init();

  initDraggables();
  restorePanelSizes();
  initSettingsModal();
  initLogPanel();

  // Give browser time to finish flex layout before Leaflet measures
  setTimeout(() => { NMMap.invalidate(); NMTelemetry.checkWidth(); }, 400);

  startPollLoop(_cfg.display.poll_interval_seconds);
  startListenerStatusLoop();
}

// ── Geolocation ─────────────────────────────────────────────────

function _initGeolocation() {
  if (!navigator.geolocation) return;
  navigator.geolocation.watchPosition(
    pos => {
      _browserPos.lat = pos.coords.latitude;
      _browserPos.lon = pos.coords.longitude;
    },
    err => console.warn("Geolocation:", err.message),
    { enableHighAccuracy: true, maximumAge: 30000, timeout: 15000 }
  );
}

function _geoQuery() {
  if (_browserPos.lat == null || _browserPos.lon == null) return "";
  return `?blat=${_browserPos.lat}&blon=${_browserPos.lon}`;
}

// ── Poll loop ───────────────────────────────────────────────────

function startPollLoop(intervalSec) {
  if (_pollTimer) clearInterval(_pollTimer);
  pollAll();
  _pollTimer = setInterval(pollAll, intervalSec * 1000);
}

async function pollAll() {
  try {
    const gq = _geoQuery();
    const [nodesResp, listsResp, timelineResp, gaugesResp] = await Promise.all([
      fetch(`/api/nodes${gq}`),
      fetch(`/api/telemetry_lists${gq}`),
      fetch("/api/timeline"),
      fetch(`/api/gauges${gq}`),
    ]);

    const [nodesData, listsData, timelineData, gaugesData] = await Promise.all([
      nodesResp.json(),
      listsResp.json(),
      timelineResp.json(),
      gaugesResp.json(),
    ]);

    document.dispatchEvent(new CustomEvent("nm:nodes-updated",    { detail: nodesData }));
    document.dispatchEvent(new CustomEvent("nm:lists-updated",    { detail: listsData }));
    document.dispatchEvent(new CustomEvent("nm:timeline-updated", { detail: timelineData }));
    document.dispatchEvent(new CustomEvent("nm:gauges-updated",   { detail: gaugesData }));

    _lastPollOk = Date.now();
    setStatus(`${nodesData.nodes.length} nodes · updated ${_timeStr()}`);
  } catch (err) {
    setStatus(`Poll error — ${err.message}`);
  }
}

// ── Settings fetch/save ─────────────────────────────────────────

async function fetchSettings() {
  const resp = await fetch("/api/settings");
  return resp.json();
}

async function saveSettings(patch) {
  const resp = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  const data = await resp.json();
  if (!resp.ok) return { errors: data.errors };
  _cfg = data.updated;
  applyTheme(_cfg);
  document.dispatchEvent(new CustomEvent("nm:settings-loaded", { detail: { cfg: _cfg } }));
  startPollLoop(_cfg.display.poll_interval_seconds);
  return {};
}

// ── Theme ───────────────────────────────────────────────────────

function applyTheme(cfg) {
  document.body.className = `theme-${cfg.display.theme}`;
}

// ── Status bar ──────────────────────────────────────────────────

function setStatus(msg) {
  const el = document.getElementById("nm-status");
  if (el) el.textContent = msg;
}

function _timeStr() {
  if (!_lastPollOk) return "never";
  const d = new Date(_lastPollOk);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// ── Listener status polling ─────────────────────────────────────

function startListenerStatusLoop() {
  _pollListenerStatus();
  setInterval(_pollListenerStatus, 5000);
}

async function _pollListenerStatus() {
  try {
    const resp = await fetch("/api/listener/status");
    const data = await resp.json();
    _applyListenerStatus(data);
    _setLogSessionName(data.session_name);
  } catch (_) {}
}

function _applyListenerStatus(data) {
  const pill  = document.getElementById("nm-listener-status");
  const dot   = document.getElementById("nm-listener-dot");
  const label = document.getElementById("nm-listener-label");
  if (!pill) return;

  pill.className = "";
  if (data.state === "running") {
    pill.classList.add("nm-listener-running");
    label.textContent = "LISTENER RUNNING";
  } else if (data.state === "error") {
    pill.classList.add("nm-listener-error");
    label.textContent = "LISTENER ERROR";
  } else {
    pill.classList.add("nm-listener-stopped");
    label.textContent = "LISTENER STOPPED";
  }

  // Update modal listener section if open
  const stateEl = document.getElementById("cfg-listener-state");
  const sessEl  = document.getElementById("cfg-listener-session");
  const connEl  = document.getElementById("cfg-listener-conn");
  if (stateEl) stateEl.textContent = data.state.toUpperCase();
  if (sessEl)  sessEl.textContent  = data.session_name || "—";
  if (connEl && data.connection) {
    const c = data.connection;
    if (c.type === "tcp")    connEl.textContent = `TCP ${c.host}:${c.port || 4403}`;
    else if (c.type === "serial") connEl.textContent = `Serial ${c.dev || "auto"}`;
    else if (c.type === "ble")    connEl.textContent = `BLE ${c.address || "auto"}`;
    else connEl.textContent = c.type || "—";
  }
}

// ── Log panel ───────────────────────────────────────────────────

function initLogPanel() {
  const btn = document.getElementById("nm-log-btn");
  btn.addEventListener("click", () => {
    _logVisible = !_logVisible;
    document.getElementById("nm-log-panel").classList.toggle("hidden", !_logVisible);
    btn.classList.toggle("active", _logVisible);
    NMMap.invalidate();
    if (_logVisible) {
      _pollLog();
      if (!_logTimer) _logTimer = setInterval(_pollLog, 3000);
    } else {
      clearInterval(_logTimer);
      _logTimer = null;
    }
  });
}

async function _pollLog() {
  try {
    const resp = await fetch("/api/listener/log?n=80");
    const data = await resp.json();
    _renderLog(data.lines);
  } catch (_) {}
}

let _logSessionName = "";

function _renderLog(lines) {
  const body = document.getElementById("nm-log-body");
  if (!body) return;

  // Only auto-scroll if user is within 60px of the bottom
  const nearBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 60;

  body.innerHTML = lines.map(l => {
    let cls = "dim";
    if (l.includes(" RECV "))                              cls = "recv";
    else if (l.includes("ERROR") || l.includes("WARNING")) cls = "warn";
    return `<div class="nm-log-line ${cls}">${_escHtml(l)}</div>`;
  }).join("");

  if (nearBottom) body.scrollTop = body.scrollHeight;

  const sessLabel = document.getElementById("nm-log-session");
  if (sessLabel && _logSessionName) sessLabel.textContent = _logSessionName;
}

// Called when listener status is fetched
function _setLogSessionName(name) { _logSessionName = name || ""; }

function _escHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ── Draggable dividers ──────────────────────────────────────────

function initDraggables() {
  const vDiv          = document.getElementById("nm-divider-v");
  const hDiv          = document.getElementById("nm-divider-h");
  const mapPanel      = document.getElementById("nm-map-panel");
  const timelinePanel = document.getElementById("nm-timeline-panel");

  _makeDraggable(vDiv, (dx, _dy) => {
    let newW = mapPanel.offsetWidth + dx;
    const minW = 200;
    const maxW = document.body.clientWidth - 150;
    newW = Math.max(minW, Math.min(maxW, newW));
    mapPanel.style.width = newW + "px";
    NMMap.invalidate();
    NMTelemetry.checkWidth();
    savePanelSizes();
  });

  _makeDraggable(hDiv, (_dx, dy) => {
    let newH = timelinePanel.offsetHeight - dy;
    newH = Math.max(60, Math.min(300, newH));
    timelinePanel.style.height = newH + "px";
    NMMap.invalidate();
    savePanelSizes();
  });
}

function _makeDraggable(el, onDrag) {
  // Mouse (desktop)
  el.addEventListener("mousedown", (e) => {
    e.preventDefault();
    let lastX = e.clientX;
    let lastY = e.clientY;
    const onMove = (e2) => {
      const dx = e2.clientX - lastX;
      const dy = e2.clientY - lastY;
      lastX = e2.clientX;
      lastY = e2.clientY;
      onDrag(dx, dy);
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });

  // Touch (iPad / iPhone)
  el.addEventListener("touchstart", (e) => {
    e.preventDefault();
    const t = e.touches[0];
    let lastX = t.clientX;
    let lastY = t.clientY;
    const onMove = (e2) => {
      const t2 = e2.touches[0];
      const dx = t2.clientX - lastX;
      const dy = t2.clientY - lastY;
      lastX = t2.clientX;
      lastY = t2.clientY;
      onDrag(dx, dy);
    };
    const onUp = () => {
      document.removeEventListener("touchmove", onMove);
      document.removeEventListener("touchend", onUp);
    };
    document.addEventListener("touchmove", onMove, { passive: false });
    document.addEventListener("touchend", onUp);
  }, { passive: false });
}

// ── Panel size persistence ──────────────────────────────────────

function savePanelSizes() {
  const mapPanel      = document.getElementById("nm-map-panel");
  const timelinePanel = document.getElementById("nm-timeline-panel");
  localStorage.setItem("nm-map-width",       mapPanel.offsetWidth);
  localStorage.setItem("nm-timeline-height", timelinePanel.offsetHeight);
}

function restorePanelSizes() {
  const mapW      = localStorage.getItem("nm-map-width");
  const timelineH = localStorage.getItem("nm-timeline-height");
  if (mapW)      document.getElementById("nm-map-panel").style.width       = mapW + "px";
  if (timelineH) document.getElementById("nm-timeline-panel").style.height = timelineH + "px";
  NMMap.invalidate();
}

// ── Settings modal ──────────────────────────────────────────────

let _activeSessions  = [];
let _activeSessionId = null;

function initSettingsModal() {
  const overlay = document.getElementById("nm-modal-overlay");
  const openBtn = document.getElementById("nm-settings-btn");
  const closeBtn = document.getElementById("nm-modal-close");
  const saveBtn  = document.getElementById("nm-modal-save");
  const errEl    = document.getElementById("nm-modal-errors");

  openBtn.addEventListener("click", async () => {
    populateModal(_cfg);
    await _loadSessions();
    await _pollListenerStatus();
    overlay.classList.remove("hidden");
  });
  closeBtn.addEventListener("click", () => overlay.classList.add("hidden"));
  overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.classList.add("hidden"); });

  saveBtn.addEventListener("click", async () => {
    errEl.textContent = "";
    const patch = readModal();
    const result = await saveSettings(patch);
    if (result.errors && result.errors.length) {
      errEl.textContent = result.errors.join("; ");
    } else {
      overlay.classList.add("hidden");
    }
  });

  _initNewSessionForm();
  _initListenerButtons();
  _initListDragging();
}

// ── Sessions in modal ───────────────────────────────────────────

async function _loadSessions() {
  try {
    const resp = await fetch("/api/sessions");
    const data = await resp.json();
    _activeSessions  = data.sessions;
    _activeSessionId = data.active_id;
    _renderSessions();
  } catch (_) {}
}

function _renderSessions() {
  const container = document.getElementById("cfg-sessions-list");
  if (!container) return;

  if (!_activeSessions.length) {
    container.innerHTML = `<div class="hint">No sessions yet.</div>`;
    return;
  }

  container.innerHTML = _activeSessions.map(s => {
    const isActive = s.id === _activeSessionId;
    const c = s.connection || {};
    let connStr = "";
    if (c.type === "tcp")    connStr = `TCP ${c.host}:${c.port || 4403}`;
    else if (c.type === "serial") connStr = `Serial ${c.dev || "auto"}`;
    else if (c.type === "ble")    connStr = `BLE ${c.address || "auto"}`;

    const created = new Date(s.created * 1000).toLocaleDateString();

    return `<div class="nm-session-row ${isActive ? "active-session" : ""}">
      <div>
        <div class="nm-session-name">${_escHtml(s.name)}
          ${isActive ? `<span class="nm-session-active-badge">ACTIVE</span>` : ""}
        </div>
        <div class="nm-session-meta">${connStr} · ${created}</div>
      </div>
      <div style="margin-left:auto;display:flex;gap:4px">
        ${!isActive ? `<button class="nm-text-btn" onclick="_activateSession('${s.id}')">Activate</button>` : ""}
        ${!isActive ? `<button class="nm-text-btn" style="color:var(--red)" onclick="_deleteSession('${s.id}','${_escHtml(s.name)}')">Del</button>` : ""}
      </div>
    </div>`;
  }).join("");
}

async function _activateSession(sessionId) {
  const resp = await fetch(`/api/sessions/${sessionId}/activate`, { method: "POST" });
  const data = await resp.json();
  if (!resp.ok) { alert(data.error); return; }
  await _loadSessions();
  pollAll();
}

async function _deleteSession(sessionId, name) {
  if (!confirm(`Delete session "${name}"? The database file will NOT be deleted.`)) return;
  const resp = await fetch(`/api/sessions/${sessionId}`, { method: "DELETE" });
  const data = await resp.json();
  if (!resp.ok) { alert(data.error); return; }
  await _loadSessions();
}

// Expose to inline onclick handlers
window._activateSession = _activateSession;
window._deleteSession   = _deleteSession;

function _initNewSessionForm() {
  const openBtn  = document.getElementById("cfg-new-session-btn");
  const form     = document.getElementById("cfg-new-session-form");
  const cancelBtn = document.getElementById("ns-cancel-btn");
  const createBtn = document.getElementById("ns-create-btn");
  const typeEl   = document.getElementById("ns-type");

  openBtn.addEventListener("click", () => {
    form.classList.toggle("hidden");
  });
  cancelBtn.addEventListener("click", () => {
    form.classList.add("hidden");
    document.getElementById("ns-error").textContent = "";
  });

  // Show/hide connection sub-fields based on type
  typeEl.addEventListener("change", _updateConnFields);

  createBtn.addEventListener("click", async () => {
    const errEl = document.getElementById("ns-error");
    errEl.textContent = "";

    const name = document.getElementById("ns-name").value.trim();
    const type = document.getElementById("ns-type").value;
    const seed = document.getElementById("ns-seed").checked;

    let connection = { type };
    if (type === "tcp") {
      connection.host = document.getElementById("ns-host").value.trim();
      connection.port = parseInt(document.getElementById("ns-port").value) || 4403;
    } else if (type === "serial") {
      const dev = document.getElementById("ns-dev").value.trim();
      if (dev) connection.dev = dev;
    } else if (type === "ble") {
      const addr = document.getElementById("ns-ble-address").value.trim();
      if (addr) connection.address = addr;
    }

    const resp = await fetch("/api/sessions/new", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, connection, seed_on_connect: seed }),
    });
    const data = await resp.json();
    if (!resp.ok) { errEl.textContent = data.error; return; }

    form.classList.add("hidden");
    document.getElementById("ns-name").value = "";
    await _loadSessions();
    pollAll();
  });
}

function _updateConnFields() {
  const type = document.getElementById("ns-type").value;
  document.getElementById("ns-tcp-fields").classList.toggle("hidden",    type !== "tcp");
  document.getElementById("ns-serial-fields").classList.toggle("hidden", type !== "serial");
  document.getElementById("ns-ble-fields").classList.toggle("hidden",    type !== "ble");
}

// ── Listener control in modal ───────────────────────────────────

function _initListenerButtons() {
  document.getElementById("cfg-listener-start").addEventListener("click", async () => {
    await fetch("/api/listener/start", { method: "POST" });
    await _pollListenerStatus();
  });
  document.getElementById("cfg-listener-stop").addEventListener("click", async () => {
    await fetch("/api/listener/stop", { method: "POST" });
    await _pollListenerStatus();
  });
}

// ── Modal: display settings ─────────────────────────────────────

function populateModal(cfg) {
  const tl = cfg.telemetry_lists || {};
  const enabled = tl.enabled_lists || [];
  const limits = tl.limits || {};

  const myId = cfg.my_node_id;
  document.getElementById("cfg-my-node-id").value =
    myId ? `!${(myId >>> 0).toString(16).padStart(8, "0")}` : "";

  document.getElementById("cfg-center-mode").value = cfg.map.center_mode;
  document.getElementById("cfg-node-labels").value = cfg.map.node_labels;
  document.getElementById("cfg-zoom").value = cfg.map.zoom;

  // Reorder labels to match saved enabled_lists order (enabled first, rest after)
  const container = document.getElementById("cfg-lists-order");
  const allIds = Array.from(container.querySelectorAll("input[type=checkbox]"))
    .map(cb => cb.dataset.list);
  const ordered = [
    ...enabled.filter(id => allIds.includes(id)),
    ...allIds.filter(id => !enabled.includes(id)),
  ];
  ordered.forEach(id => {
    const label = container.querySelector(`input[data-list="${id}"]`)?.closest("label");
    if (label) container.appendChild(label);
  });

  document.querySelectorAll("#cfg-lists-order input[type=checkbox]").forEach(cb => {
    cb.checked = enabled.includes(cb.dataset.list);
  });
  document.querySelectorAll(".limit-input").forEach(inp => {
    inp.value = limits[inp.dataset.list] || 5;
  });

  document.getElementById("cfg-time-scale").value = cfg.timeline.time_scale_hours;
  document.getElementById("cfg-y-axis-mode").value = cfg.timeline.y_axis_mode;
  document.getElementById("cfg-y-axis-max").value = cfg.timeline.y_axis_max;
  document.getElementById("cfg-msg-accent").checked = cfg.timeline.message_accent;

  document.getElementById("cfg-theme").value = cfg.display.theme;
  document.getElementById("cfg-poll-interval").value = cfg.display.poll_interval_seconds;
}

function readModal() {
  const rawId = document.getElementById("cfg-my-node-id").value.trim();
  let myNodeId = null;
  if (rawId) {
    const hex = rawId.startsWith("!") ? rawId.slice(1) : rawId;
    const parsed = parseInt(hex, 16);
    if (!isNaN(parsed)) myNodeId = parsed >>> 0;
  }

  const enabled = [];
  const limits = {};
  document.querySelectorAll("#cfg-lists-order input[type=checkbox]").forEach(cb => {
    if (cb.checked) enabled.push(cb.dataset.list);
  });
  document.querySelectorAll(".limit-input").forEach(inp => {
    limits[inp.dataset.list] = parseInt(inp.value) || 5;
  });

  return {
    my_node_id: myNodeId,
    telemetry_lists: { enabled_lists: enabled, limits },
    timeline: {
      time_scale_hours: parseInt(document.getElementById("cfg-time-scale").value),
      y_axis_mode: document.getElementById("cfg-y-axis-mode").value,
      y_axis_max: parseInt(document.getElementById("cfg-y-axis-max").value) || 30,
      message_accent: document.getElementById("cfg-msg-accent").checked,
    },
    map: {
      center_mode: document.getElementById("cfg-center-mode").value,
      node_labels: document.getElementById("cfg-node-labels").value,
      zoom: parseInt(document.getElementById("cfg-zoom").value),
    },
    display: {
      theme: document.getElementById("cfg-theme").value,
      poll_interval_seconds: parseInt(document.getElementById("cfg-poll-interval").value),
    },
  };
}

// ── List drag-to-reorder ─────────────────────────────────────────

function _initListDragging() {
  const container = document.getElementById("cfg-lists-order");
  let _dragSrc = null;

  // ── Mouse drag (Chrome / desktop) ──────────────────────────────
  container.addEventListener("dragstart", e => {
    _dragSrc = e.target.closest("label");
    if (!_dragSrc) return;
    e.dataTransfer.effectAllowed = "move";
    setTimeout(() => _dragSrc.classList.add("dragging"), 0);
  });

  container.addEventListener("dragover", e => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    const target = e.target.closest("label");
    if (!target || target === _dragSrc) return;
    const rect = target.getBoundingClientRect();
    if (e.clientY < rect.top + rect.height / 2) {
      container.insertBefore(_dragSrc, target);
    } else {
      container.insertBefore(_dragSrc, target.nextSibling);
    }
  });

  container.addEventListener("dragend", () => {
    if (_dragSrc) _dragSrc.classList.remove("dragging");
    _dragSrc = null;
  });

  // ── Touch drag (Safari / iPad) ──────────────────────────────────
  let _touchSrc = null;

  container.addEventListener("touchstart", e => {
    if (!e.target.closest(".drag-handle")) return;
    _touchSrc = e.target.closest("label");
    if (!_touchSrc) return;
    _touchSrc.classList.add("dragging");
    e.preventDefault();
  }, { passive: false });

  container.addEventListener("touchmove", e => {
    if (!_touchSrc) return;
    e.preventDefault();
    const touch = e.touches[0];
    // Briefly hide the dragged row so elementFromPoint sees what's behind it
    _touchSrc.style.visibility = "hidden";
    const below = document.elementFromPoint(touch.clientX, touch.clientY);
    _touchSrc.style.visibility = "";
    const target = below?.closest("label");
    if (!target || target === _touchSrc) return;
    const rect = target.getBoundingClientRect();
    if (touch.clientY < rect.top + rect.height / 2) {
      container.insertBefore(_touchSrc, target);
    } else {
      container.insertBefore(_touchSrc, target.nextSibling);
    }
  }, { passive: false });

  container.addEventListener("touchend", () => {
    if (_touchSrc) _touchSrc.classList.remove("dragging");
    _touchSrc = null;
  });
}
