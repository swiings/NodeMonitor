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
  // Prevent browser pinch-to-zoom on the whole page.
  // On macOS Chrome, trackpad pinch = wheel event with ctrlKey.
  // On iOS Safari, it's a touchmove with 2 fingers (Safari ignores user-scalable=no).
  // Leaflet map zoom is unaffected — it's implemented in JS, not a browser default.
  document.addEventListener("wheel", (e) => {
    if (e.ctrlKey) e.preventDefault();
  }, { passive: false });
  document.addEventListener("touchmove", (e) => {
    if (e.touches.length > 1) e.preventDefault();
  }, { passive: false });

  // Register all UI handlers first — before any fetch() that might hang or throw.
  initDraggables();
  initSettingsModal();
  initLogPanel();
  initCsvModal();
  initBeep();

  try {
    _cfg = await fetchSettings();
  } catch (_) {
    _cfg = {};
  }
  applyTheme(_cfg);

  document.dispatchEvent(new CustomEvent("nm:settings-loaded", { detail: { cfg: _cfg } }));

  _initGeolocation();

  NMMap.init("nm-map");
  NMTelemetry.init();
  NMTimeline.init("nm-timeline-canvas");
  NMGauges.init();

  restorePanelSizes();
  initTrafficWindow();

  // Give browser time to finish flex layout before Leaflet measures
  setTimeout(() => { NMMap.invalidate(); NMTelemetry.checkWidth(); }, 400);

  document.addEventListener("nm:request-poll", () => pollAll());

  startPollLoop((_cfg.display || {}).poll_interval_seconds || 30);
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
  // Close any open node info popups so stale data doesn't linger
  // NMMap.closePopups();
  // const nodeInfoPopup = document.getElementById("nm-node-info-popup");
  // if (nodeInfoPopup) nodeInfoPopup.remove();

  try {
    const gq = _geoQuery();
    const [nodesResp, listsResp, timelineResp, gaugesResp, brkResp] = await Promise.all([
      fetch(`/api/nodes${gq}`),
      fetch(`/api/telemetry_lists${gq}`),
      fetch("/api/timeline"),
      fetch(`/api/gauges${gq}`),
      fetch("/api/packet_breakdown"),
    ]);

    const [nodesData, listsData, timelineData, gaugesData, brkData] = await Promise.all([
      nodesResp.json(),
      listsResp.json(),
      timelineResp.json(),
      gaugesResp.json(),
      brkResp.json(),
    ]);

    document.dispatchEvent(new CustomEvent("nm:nodes-updated",     { detail: nodesData }));
    document.dispatchEvent(new CustomEvent("nm:lists-updated",     { detail: listsData }));
    document.dispatchEvent(new CustomEvent("nm:timeline-updated",  { detail: timelineData }));
    document.dispatchEvent(new CustomEvent("nm:gauges-updated",    { detail: gaugesData }));
    document.dispatchEvent(new CustomEvent("nm:breakdown-updated", { detail: brkData }));

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
  const layout = (cfg.display || {}).panel_layout || "single";
  document.body.className =
    `theme-${(cfg.display || {}).theme || "color"} layout-${layout}`;
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
  const label = document.getElementById("nm-listener-label");
  if (!pill) return;

  pill.className = "";
  if (data.state === "running") {
    pill.classList.add("nm-listener-running");
    // Show node/channel/frequency if available
    if (data.my_node_short && data.channel_num != null) {
      const freq = data.frequency_mhz != null ? ` (${data.frequency_mhz}MHz)` : "";
      label.textContent = `Listening to ${data.my_node_short} on Slot ${data.channel_num}${freq}`;
    } else {
      label.textContent = "LISTENER RUNNING";
    }
  } else if (data.state === "error") {
    pill.classList.add("nm-listener-error");
    label.textContent = "LISTENER ERROR";
  } else {
    pill.classList.add("nm-listener-stopped");
    label.textContent = "LISTENER STOPPED";
  }

  // Update modal listener section if open
  const stateEl   = document.getElementById("cfg-listener-state");
  const sessEl    = document.getElementById("cfg-listener-session");
  const connEl    = document.getElementById("cfg-listener-conn");
  const saveEl    = document.getElementById("cfg-save-data");
  const runtimeEl = document.getElementById("cfg-my-node-runtime");

  if (stateEl) stateEl.textContent = data.state.toUpperCase();
  if (sessEl)  sessEl.textContent  = data.session_name || "—";
  if (connEl && data.connection) {
    const c = data.connection;
    if (c.type === "tcp")         connEl.textContent = `TCP ${c.host}:${c.port || 4403}`;
    else if (c.type === "serial") connEl.textContent = `Serial ${c.dev || "auto"}`;
    else if (c.type === "ble")    connEl.textContent = `BLE ${c.address || "auto"}`;
    else connEl.textContent = c.type || "—";
  }
  if (saveEl && data.save_packet_data != null) {
    saveEl.checked = data.save_packet_data;
  }
  if (runtimeEl && data.my_node_short) {
    runtimeEl.textContent = `Detected: ${data.my_node_short} (${data.my_node_id ? "!" + (data.my_node_id >>> 0).toString(16).padStart(8,"0") : "—"})`;
  }
}

// ── Log panel ───────────────────────────────────────────────────

function initLogPanel() {
  const btn    = document.getElementById("nm-log-btn");
  const divLog = document.getElementById("nm-divider-log");
  btn.addEventListener("click", () => {
    _logVisible = !_logVisible;
    document.getElementById("nm-log-panel").classList.toggle("hidden", !_logVisible);
    if (divLog) divLog.classList.toggle("hidden", !_logVisible);
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
    if (l.includes("ERROR") || l.includes("WARNING")) {
      cls = "warn";
    } else if (l.includes(" RECV ")) {
      cls = "recv " + _portnumClass(l);
    }
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

// Derive a CSS class from the portnum token in a RECV log line.
// Log format: "[timestamp] RECV PORTNUM_NAME   from=..."
function _portnumClass(line) {
  const m = line.match(/RECV\s+(\S+)/);
  if (!m) return "pkt-other";
  const pn = m[1];
  if (pn === "TEXT_MESSAGE_APP")   return "pkt-text";
  if (pn === "POSITION_APP")       return "pkt-position";
  if (pn === "TELEMETRY_APP")      return "pkt-telemetry";
  if (pn === "NODEINFO_APP")       return "pkt-nodeinfo";
  if (pn === "UNKNOWN")            return "pkt-unknown";
  if (pn === "UNKNOWN_PRIVATE_CH") return "pkt-private";       
  if (pn === "UNKNOWN_DIRECT")     return "pkt-direct";    
  if (pn === "STORE_FORWARD_APP")  return "pkt-forward";   
  if (pn === "NEIGHBORINFO_APP")   return "pkt-neighbor";   
  if (pn === "RANGE_TEST_APP")     return "pkt-range"; 
  if (pn === "ADMIN_APP")          return "pkt-admin";
  if (pn === "PAXCOUNTER_APP")     return "pkt-pax";

  return "pkt-other";
}

// ── Traffic Log window ───────────────────────────────────────────

function initTrafficWindow() {
  const win      = document.getElementById("nm-traffic-window");
  const closeBtn = document.getElementById("nm-traffic-close");
  const header   = document.getElementById("nm-traffic-header");
  if (!win || !closeBtn || !header) return;

  closeBtn.addEventListener("click", () => {
    win.classList.add("hidden");
    document.getElementById("nm-traffic-body").innerHTML = "";
    document.getElementById("nm-traffic-detail").classList.add("hidden");
  });

  document.getElementById("nm-traffic-detail-close").addEventListener("click", () => {
    document.getElementById("nm-traffic-detail").classList.add("hidden");
    document.querySelectorAll(".nm-traffic-row--selected").forEach(r => r.classList.remove("nm-traffic-row--selected"));
  });

  document.addEventListener("nm:open-traffic", async (e) => {
    const { by, value, label } = e.detail;
    await _openTrafficWindow(by, value, label);
  });

  // Make window draggable by its header
  let _dragX = 0, _dragY = 0;
  header.addEventListener("mousedown", (e) => {
    if (e.target === closeBtn) return;
    e.preventDefault();
    const rect = win.getBoundingClientRect();
    // Switch from transform-based centering to fixed coordinates
    win.style.transform = "none";
    win.style.left = `${rect.left}px`;
    win.style.top  = `${rect.top}px`;
    _dragX = e.clientX - rect.left;
    _dragY = e.clientY - rect.top;
    const move = (e2) => {
      win.style.left = `${e2.clientX - _dragX}px`;
      win.style.top  = `${e2.clientY - _dragY}px`;
    };
    const up = () => {
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  });
}

const _TRAFFIC_PKT_COLORS = {
  "TEXT_MESSAGE_APP":   "var(--red)",
  "POSITION_APP":       "var(--green)",
  "TELEMETRY_APP":      "var(--accent)",
  "NODEINFO_APP":       "var(--purple)",
  "UNKNOWN_PRIVATE_CH": "#6dbad5",
  "UNKNOWN_DIRECT":     "#afefd8",
  "STORE_FORWARD_APP":  "#bddf34",
  "NEIGHBORINFO_APP":   "#73eb3b",
  "UNKNOWN":            "#f5c2c2",
};

async function _openTrafficWindow(by, value, label) {
  const win   = document.getElementById("nm-traffic-window");
  const body  = document.getElementById("nm-traffic-body");
  const title = document.getElementById("nm-traffic-title");
  if (!win || !body || !title) return;

  title.textContent = `TRAFFIC: ${label}`;
  body.innerHTML = `<div style="padding:10px;color:var(--text-dim);font-size:11px">Loading…</div>`;

  // Reset to centered position each time
  win.style.transform = "translateX(-50%)";
  win.style.left = "50%";
  win.style.top  = "90px";
  win.classList.remove("hidden");

  try {
    const resp = await fetch(`/api/traffic?by=${encodeURIComponent(by)}&value=${encodeURIComponent(value)}`);
    const data = await resp.json();
    _renderTrafficBody(body, data.packets || []);
  } catch (err) {
    body.innerHTML = `<div style="padding:10px;color:var(--red);font-size:11px">Error: ${_escHtml(err.message)}</div>`;
  }
}

function _renderTrafficBody(body, packets) {
  if (packets.length === 0) {
    body.innerHTML = `<div style="padding:10px;color:var(--text-dim);font-size:11px">No packets found.</div>`;
    return;
  }
  const hdr = `<div class="nm-traffic-row nm-traffic-hdr">
    <span class="nm-traffic-col" style="color:var(--text-dim)">Time</span>
    <span class="nm-traffic-col" style="color:var(--text-dim)">From</span>
    <span class="nm-traffic-col" style="color:var(--text-dim)">Type</span>
    <span class="nm-traffic-col" style="color:var(--text-dim);text-align:right">RSSI</span>
    <span class="nm-traffic-col" style="color:var(--text-dim);text-align:right">SNR</span>
  </div>`;

  const frag = document.createDocumentFragment();
  const hdrEl = document.createElement("div");
  hdrEl.innerHTML = hdr;
  frag.appendChild(hdrEl.firstElementChild);

  for (const p of packets) {
    const ts    = p.rx_time ? new Date(p.rx_time * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }) : "—";
    const from  = _escHtml(p.from_display || p.from_node_id_hex || "—");
    const type  = p.portnum ? p.portnum.replace(/_APP$/, "").replace(/_/g, " ") : "?";
    const color = _TRAFFIC_PKT_COLORS[p.portnum] || "var(--text-dim)";
    const rssi  = p.rx_rssi != null ? p.rx_rssi : "—";
    const snr   = p.rx_snr  != null ? p.rx_snr  : "—";

    const row = document.createElement("div");
    row.className = "nm-traffic-row";
    row.style.cursor = "pointer";
    row.innerHTML = `
      <span class="nm-traffic-col">${ts}</span>
      <span class="nm-traffic-col" style="color:var(--accent)">${from}</span>
      <span class="nm-traffic-col" style="color:${color}">${type}</span>
      <span class="nm-traffic-col" style="text-align:right">${rssi}</span>
      <span class="nm-traffic-col" style="text-align:right">${snr}</span>`;
    row.addEventListener("click", () => _showTrafficDetail(p, row));
    frag.appendChild(row);
  }

  body.innerHTML = "";
  body.appendChild(frag);
}

function _showTrafficDetail(p, rowEl) {
  document.querySelectorAll(".nm-traffic-row--selected").forEach(r => r.classList.remove("nm-traffic-row--selected"));
  rowEl.classList.add("nm-traffic-row--selected");

  const BROADCAST = 4294967295;
  const _hex = id => id != null ? `!${(id >>> 0).toString(16).padStart(8, "0")}` : "—";
  const _ts  = t  => t  ? new Date(t * 1000).toLocaleString([], { hour12: false }) : "—";

  const toId  = p.to_node_id;
  const toStr = toId === BROADCAST ? "Broadcast (0xFFFFFFFF)"
              : toId != null       ? _hex(toId)
              : "—";

  const hopsUsed = p.hop_limit != null ? (3 - p.hop_limit) : null;
  const hopStr   = p.hop_limit != null
    ? `${p.hop_limit} remaining (${hopsUsed >= 0 ? hopsUsed : "?"} used)`
    : "—";

  const fromFull = [p.short_name, p.long_name].filter(Boolean);
  const fromStr  = fromFull.length
    ? `${_hex(p.from_node_id)}  ${fromFull.join(" / ")}`
    : _hex(p.from_node_id);

  const typeStr = p.portnum
    ? p.portnum.replace(/_APP$/, "").replace(/_/g, " ")
    : "—";

  const fields = [
    ["Time",       _ts(p.rx_time)],
    ["From",       fromStr],
    ["To",         toStr],
    ["Type",       typeStr],
    ["RSSI",       p.rx_rssi != null ? `${p.rx_rssi} dBm` : "—"],
    ["SNR",        p.rx_snr  != null ? `${p.rx_snr} dB`   : "—"],
    ["Hop Limit",  hopStr],
  ];

  const detail = document.getElementById("nm-traffic-detail");
  const detailBody = document.getElementById("nm-traffic-detail-body");
  detailBody.innerHTML = fields.map(([k, v]) =>
    `<span class="nm-detail-key">${k}</span><span class="nm-detail-val">${_escHtml(String(v))}</span>`
  ).join("");
  detail.classList.remove("hidden");
}

// ── Draggable dividers ──────────────────────────────────────────

function initDraggables() {
  const vDiv          = document.getElementById("nm-divider-v");
  const hDiv          = document.getElementById("nm-divider-h");
  const logDiv        = document.getElementById("nm-divider-log");
  const mapPanel      = document.getElementById("nm-map-panel");
  const timelineRow   = document.getElementById("nm-timeline-row");
  const logPanel      = document.getElementById("nm-log-panel");

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
    let newH = timelineRow.offsetHeight - dy;
    newH = Math.max(60, Math.min(300, newH));
    timelineRow.style.height = newH + "px";
    NMMap.invalidate();
    savePanelSizes();
  });

  _makeDraggable(logDiv, (_dx, dy) => {
    let newH = logPanel.offsetHeight - dy;
    const maxH = Math.floor(window.innerHeight * 0.5);
    newH = Math.max(60, Math.min(maxH, newH));
    logPanel.style.height = newH + "px";
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
  const mapPanel    = document.getElementById("nm-map-panel");
  const timelineRow = document.getElementById("nm-timeline-row");
  const logPanel    = document.getElementById("nm-log-panel");
  localStorage.setItem("nm-map-width",       mapPanel.offsetWidth);
  localStorage.setItem("nm-timeline-height", timelineRow.offsetHeight);
  if (!logPanel.classList.contains("hidden")) {
    localStorage.setItem("nm-log-height", logPanel.offsetHeight);
  }
}

function restorePanelSizes() {
  const mapW      = localStorage.getItem("nm-map-width");
  const timelineH = localStorage.getItem("nm-timeline-height");
  const logH      = localStorage.getItem("nm-log-height");
  if (mapW)      document.getElementById("nm-map-panel").style.width      = mapW + "px";
  if (timelineH) document.getElementById("nm-timeline-row").style.height  = timelineH + "px";
  if (logH)      document.getElementById("nm-log-panel").style.height     = logH + "px";
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
    try { populateModal(_cfg); } catch (_) {}
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
  _initSaveDataToggle();
  _initRequeuButton();
  initActivateModal();
}

function _initSaveDataToggle() {
  const cb = document.getElementById("cfg-save-data");
  if (!cb) return;
  cb.addEventListener("change", async () => {
    await fetch("/api/sessions/active/save_packet_data", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ save_packet_data: cb.checked }),
    });
  });
}

function _initRequeuButton() {
  const btn    = document.getElementById("cfg-requeue-nodes");
  const status = document.getElementById("cfg-requeue-status");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    if (status) status.textContent = "Queuing…";
    try {
      await fetch("/api/listener/requeue-nodes", { method: "POST" });
      if (status) status.textContent = "Queued!";
    } catch (_) {
      if (status) status.textContent = "Error";
    }
    setTimeout(() => {
      btn.disabled = false;
      if (status) status.textContent = "";
    }, 3000);
  });
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
        ${!isActive ? `<button class="nm-text-btn" onclick="_showActivateModal('${s.id}')">Activate</button>` : ""}
        ${!isActive ? `<button class="nm-text-btn" style="color:var(--red)" onclick="_deleteSession('${s.id}','${_escHtml(s.name)}')">Del</button>` : ""}
      </div>
    </div>`;
  }).join("");
}

async function _deleteSession(sessionId, name) {
  if (!confirm(`Delete session "${name}"? The database file will NOT be deleted.`)) return;
  const resp = await fetch(`/api/sessions/${sessionId}`, { method: "DELETE" });
  const data = await resp.json();
  if (!resp.ok) { alert(data.error); return; }
  await _loadSessions();
}

// Expose to inline onclick handlers
window._deleteSession   = _deleteSession;

// ── Activate Session modal ──────────────────────────────────────

let _activatingSessionId = null;

function initActivateModal() {
  const modal      = document.getElementById("nm-activate-modal");
  const closeBtn   = document.getElementById("nm-activate-close");
  const cancelBtn  = document.getElementById("af-cancel-btn");
  const activateBtn = document.getElementById("af-activate-btn");

  if (!modal || !closeBtn || !cancelBtn || !activateBtn) return;

  const _hide = () => {
    modal.classList.add("hidden");
    document.getElementById("af-error").textContent = "";
    _activatingSessionId = null;
  };

  closeBtn.addEventListener("click", _hide);
  cancelBtn.addEventListener("click", _hide);
  modal.addEventListener("click", e => { if (e.target === modal) _hide(); });

  activateBtn.addEventListener("click", async () => {
    if (!_activatingSessionId) return;
    const errEl = document.getElementById("af-error");
    errEl.textContent = "";

    const resp = await fetch(`/api/sessions/${_activatingSessionId}/activate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const data = await resp.json();
    if (!resp.ok) { errEl.textContent = data.error; return; }

    _hide();
    await _loadSessions();
    pollAll();
  });
}

function _showActivateModal(sessionId) {
  const modal = document.getElementById("nm-activate-modal");
  if (!modal) return;
  const session = _activeSessions.find(s => s.id === sessionId);
  if (!session) return;
  _activatingSessionId = sessionId;

  document.getElementById("nm-activate-title").textContent = `Activate: ${session.name}`;
  document.getElementById("af-error").textContent = "";
  modal.classList.remove("hidden");
}
window._showActivateModal = _showActivateModal;

function _initNewSessionForm() {
  const openBtn   = document.getElementById("cfg-new-session-btn");
  const form      = document.getElementById("cfg-new-session-form");
  const cancelBtn = document.getElementById("ns-cancel-btn");
  const createBtn = document.getElementById("ns-create-btn");

  // ── Connection type button group ────────────────────────────────
  let _activeConnType = null;

  function _setConnType(type) {
    _activeConnType = type;
    document.querySelectorAll(".nm-conn-type-btn").forEach(btn => {
      btn.classList.toggle("active", btn.dataset.type === type);
    });
    for (const id of ["ns-serial-config", "ns-ble-config", "ns-tcp-config"]) {
      document.getElementById(id)?.classList.add("hidden");
    }
    if (type) document.getElementById(`ns-${type}-config`)?.classList.remove("hidden");
  }

  document.querySelectorAll(".nm-conn-type-btn").forEach(btn => {
    btn.addEventListener("click", () => _setConnType(btn.dataset.type));
  });

  // Default to SERIAL selected
  _setConnType("serial");

  // ── Serial scan ─────────────────────────────────────────────────
  function _setupScan(scanBtnId, statusId, resultsId, hiddenInputId, apiPath, labelFn, valueFn) {
    const scanBtn     = document.getElementById(scanBtnId);
    const statusEl   = document.getElementById(statusId);
    const resultsEl  = document.getElementById(resultsId);
    const hiddenInput = document.getElementById(hiddenInputId);
    if (!scanBtn) return;

    scanBtn.addEventListener("click", async () => {
      statusEl.textContent = "Scanning…";
      resultsEl.classList.add("hidden");
      resultsEl.innerHTML = "";
      hiddenInput.value = "";
      try {
        const resp = await fetch(apiPath);
        const data = await resp.json();
        const items = data.ports || data.devices || [];
        if (items.length === 0) {
          statusEl.textContent = "None found";
          return;
        }
        if (items.length === 1) {
          hiddenInput.value = valueFn(items[0]);
          statusEl.textContent = `Selected: ${labelFn(items[0])}`;
          return;
        }
        statusEl.textContent = `${items.length} found — pick one:`;
        resultsEl.classList.remove("hidden");
        items.forEach(item => {
          const row = document.createElement("div");
          row.className = "nm-scan-item";
          row.textContent = labelFn(item);
          row.addEventListener("click", () => {
            hiddenInput.value = valueFn(item);
            statusEl.textContent = `Selected: ${labelFn(item)}`;
            resultsEl.classList.add("hidden");
            resultsEl.querySelectorAll(".nm-scan-item").forEach(r => r.classList.remove("active"));
            row.classList.add("active");
            resultsEl.classList.remove("hidden");
          });
          resultsEl.appendChild(row);
        });
      } catch (_) {
        statusEl.textContent = "Scan failed";
      }
    });
  }

  _setupScan(
    "ns-serial-scan", "ns-serial-status", "ns-serial-results", "ns-serial-dev",
    "/api/listener/scan/serial",
    p => p.description ? `${p.device} — ${p.description}` : p.device,
    p => p.device,
  );
  _setupScan(
    "ns-ble-scan", "ns-ble-status", "ns-ble-results", "ns-ble-address",
    "/api/listener/scan/ble",
    d => d.name ? `${d.name} (${d.address})` : d.address,
    d => d.address,
  );

  // ── Build connection config from form state ─────────────────────
  function _buildConnection() {
    if (_activeConnType === "serial") {
      const dev = document.getElementById("ns-serial-dev").value.trim();
      return dev ? { type: "serial", device: dev } : { type: "serial" };
    }
    if (_activeConnType === "ble") {
      const addr = document.getElementById("ns-ble-address").value.trim();
      return addr ? { type: "ble", address: addr } : { type: "ble" };
    }
    if (_activeConnType === "tcp") {
      const host = document.getElementById("ns-tcp-host").value.trim();
      const port = parseInt(document.getElementById("ns-tcp-port").value) || 4403;
      return host ? { type: "tcp", host, port } : { type: "tcp", port };
    }
    return { type: "serial" };
  }

  // ── Open / cancel ───────────────────────────────────────────────
  openBtn.addEventListener("click", () => form.classList.toggle("hidden"));
  cancelBtn.addEventListener("click", () => {
    form.classList.add("hidden");
    document.getElementById("ns-error").textContent = "";
  });

  // ── Create ──────────────────────────────────────────────────────
  createBtn.addEventListener("click", async () => {
    const errEl = document.getElementById("ns-error");
    errEl.textContent = "";

    const name       = document.getElementById("ns-name").value.trim();
    const seed       = document.getElementById("ns-seed").checked;
    const saveData   = document.getElementById("ns-save-data")?.checked ?? true;
    const connection = _buildConnection();

    const resp = await fetch("/api/sessions/new", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, seed_on_connect: seed, save_packet_data: saveData, connection }),
    });
    const data = await resp.json();
    if (!resp.ok) { errEl.textContent = data.error; return; }

    form.classList.add("hidden");
    document.getElementById("ns-name").value = "";
    await _loadSessions();
    pollAll();
  });
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

  const tsm = cfg.timeline.time_scale_minutes || (cfg.timeline.time_scale_hours || 6) * 60;
  document.getElementById("cfg-time-scale").value = tsm;
  document.getElementById("cfg-y-axis-mode").value = cfg.timeline.y_axis_mode;
  document.getElementById("cfg-y-axis-max").value = cfg.timeline.y_axis_max;
  document.getElementById("cfg-msg-accent").checked = cfg.timeline.message_accent;

  document.getElementById("cfg-theme").value = cfg.display.theme;
  document.getElementById("cfg-poll-interval").value = cfg.display.poll_interval_seconds;
  document.getElementById("cfg-panel-layout").value = cfg.display.panel_layout || "single";
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
      time_scale_minutes: parseInt(document.getElementById("cfg-time-scale").value),
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
      panel_layout: document.getElementById("cfg-panel-layout").value,
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

// ── CSV Export modal ─────────────────────────────────────────────

function initCsvModal() {
  const csvBtn   = document.getElementById("nm-csv-btn");
  const modal    = document.getElementById("nm-csv-modal");
  const closeBtn = document.getElementById("nm-csv-modal-close");
  if (!csvBtn || !modal) return;

  csvBtn.addEventListener("click", async () => {
    await _loadCsvSessions();
    modal.classList.remove("hidden");
  });
  closeBtn.addEventListener("click", () => modal.classList.add("hidden"));
  modal.addEventListener("click", (e) => { if (e.target === modal) modal.classList.add("hidden"); });
}

async function _loadCsvSessions() {
  const list = document.getElementById("nm-csv-session-list");
  if (!list) return;
  list.innerHTML = `<li class="nm-hint-inline">Loading…</li>`;
  try {
    const resp = await fetch("/api/sessions/exportable");
    const data = await resp.json();
    list.innerHTML = (data.sessions || []).map(s => {
      const dateStr = s.created ? new Date(s.created * 1000).toLocaleString() : "—";
      const disabled = !s.db_exists ? ' class="nm-csv-no-data"' : "";
      const sid = _escHtml(s.id);
      const sname = _escHtml(s.name);
      return `<li${disabled}>
        <span class="nm-csv-name">${sname}</span>
        <span class="nm-csv-date">${dateStr}</span>
        ${s.db_exists
          ? `<button class="nm-text-btn" onclick="_downloadCsv('/api/sessions/${sid}/export/nodes.csv','nodes_${sid}.csv')">Nodes</button>
             <button class="nm-text-btn" onclick="_downloadCsv('/api/sessions/${sid}/export/packets.csv','packets_${sid}.csv')">Packets</button>`
          : `<span class="nm-hint-inline">no data</span>`}
      </li>`;
    }).join("");
  } catch (_) {
    list.innerHTML = `<li class="nm-hint-inline">Failed to load sessions</li>`;
  }
}

async function _downloadCsv(url, filename) {
  try {
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`Server error ${resp.status}`);
    const blob = await resp.blob();
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = blobUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(blobUrl); a.remove(); }, 1000);
  } catch (err) {
    alert(`CSV export failed: ${err.message}`);
  }
}
window._downloadCsv = _downloadCsv;

// ── Packet beep ─────────────────────────────────────────────────

function initBeep() {
  const slider = document.getElementById("nm-beep-volume");
  const icon   = document.getElementById("nm-beep-icon");
  if (!slider) return;

  // Restore persisted volume; default 0.5
  const saved = localStorage.getItem("nm-beep-volume");
  if (saved !== null) slider.value = saved;
  _updateBeepIcon(parseFloat(slider.value), icon);

  slider.addEventListener("mouseup", () => {
    _playBeep(parseFloat(slider.value));
  });

  slider.addEventListener("input", () => {
    const v = parseFloat(slider.value);
    localStorage.setItem("nm-beep-volume", slider.value);
    _updateBeepIcon(v, icon);
    // Resume AudioContext if user interaction just allowed it
    if (_beepCtx && _beepCtx.state === "suspended") _beepCtx.resume();
  });

  // AudioContext must be created (or resumed) inside a user-gesture handler.
  // We lazily init on the first qualifying interaction anywhere on the page.
  const _resume = () => {
    if (!_beepCtx) {
      try { _beepCtx = new (window.AudioContext || window.webkitAudioContext)(); }
      catch (_) {}
    } else if (_beepCtx.state === "suspended") {
      _beepCtx.resume();
    }
  };
  document.addEventListener("click",      _resume, { passive: true });
  document.addEventListener("keydown",    _resume, { passive: true });
  document.addEventListener("touchstart", _resume, { passive: true });

  document.addEventListener("nm:packets-received", () => {
    const v = parseFloat(slider.value);
    if (v > 0) _playBeep(v);
  });
}

let _beepCtx = null;

function _updateBeepIcon(volume, icon) {
  if (!icon) return;
  icon.textContent = volume === 0 ? "🔕" : "🔔";
}

function _playBeep(volume) {
  if (!_beepCtx || _beepCtx.state === "suspended") return;
  try {
    const t   = _beepCtx.currentTime;
    const osc = _beepCtx.createOscillator();
    const gain = _beepCtx.createGain();

    osc.connect(gain);
    gain.connect(_beepCtx.destination);

    osc.type = "sine";
    osc.frequency.setValueAtTime(1100, t);

    // Scale to a reasonable amplitude (max gain ~0.35 avoids clipping)
    const amp = volume * 0.35;
    gain.gain.setValueAtTime(amp, t);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.09);

    osc.start(t);
    osc.stop(t + 0.09);
  } catch (_) {}
}
