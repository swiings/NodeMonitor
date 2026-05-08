/**
 * telemetry.js — Telemetry list rendering.
 *
 * Listens for:
 *   "nm:lists-updated" — re-renders all configured telemetry list panels
 *
 * Features:
 *   - Drag the title bar of any card (including Hops Left / Packet Breakdown)
 *     to reorder it within the right panel.
 *   - Drag the bottom resize handle of a dynamic list card to change its
 *     displayed row count (min 1, max 10). Limit persists in localStorage.
 *   - Card order persists in localStorage.
 */

"use strict";

const NMTelemetry = (() => {
  const WIDE_THRESHOLD = 240;
  const ROW_HEIGHT     = 22;   // approx px per row for resize drag
  let _wideMode  = false;
  let _container = null;

  // Per-list display limits, keyed by list.id, persisted to localStorage
  const _displayLimits = {};
  // Full list data from the most recent server response, keyed by list.id
  const _cachedLists = new Map();

  // Static card IDs that live in the HTML (no resize handle, no data rows)
  const STATIC_IDS = ["hops_left", "pkt_breakdown", "hw_breakdown", "role_breakdown"];

  // Matches _PORTNUM_STACK colors in timeline.js
  const _PORTNUM_COLORS = {
    "TEXT_MESSAGE_APP": "rgba(138,30,26,0.9)",
    "UNKNOWN":          "rgba(112,78,10,0.9)",
    "POSITION_APP":     "rgba(24,100,46,0.9)",
    "TELEMETRY_APP":    "rgba(26,76,148,0.9)",
    "NODEINFO_APP":     "rgba(100,48,152,0.9)",
  };

  function _portnumColor(portnum) {
    return _PORTNUM_COLORS[portnum] || "rgba(44,51,60,0.95)";
  }

  const _ROLE_INT_MAP = {
    "0": "CLIENT", "1": "CLIENT_MUTE", "2": "ROUTER",
    "3": "ROUTER_CLIENT", "4": "CLIENT_HIDDEN", "5": "LOST_AND_FOUND",
    "6": "CLIENT_TRACKER", "7": "SENSOR", "8": "TAK", "9": "CLIENT_HIDDEN",
    "10": "LOST_AND_FOUND_TRACKER", "11": "SENSOR_MQTT", "12": "REPEATER",
    "13": "ROUTER_LATE",
  };

  function _emojiForRole(role) {
    const r = (_ROLE_INT_MAP[String(role ?? "CLIENT")] || String(role ?? "CLIENT")).toUpperCase();
    if (r === "CLIENT_BASE" ) return "🏡";
    if (r === "CLIENT" ) return "📱";
    if (r === "CLIENT_MUTE" ) return "😎";
    if (r === "CLIENT_HIDDEN" ) return "👻";
    if (r === "ROUTER" || r === "ROUTER_CLIENT" || r === "ROUTER_LATE" || r === "REPEATER") return "📡";
    return "📱";
  }

  // ── Init ──────────────────────────────────────────────────────

  function init() {
    _container = document.getElementById("nm-lists-container");
    _loadDisplayLimits();
    _initCardDragging();
    document.addEventListener("nm:lists-updated", (e) => _render(e.detail.lists));
  }

  // ── Persistence helpers ───────────────────────────────────────

  function _loadDisplayLimits() {
    try {
      const saved = JSON.parse(localStorage.getItem("nm-display-limits") || "{}");
      Object.assign(_displayLimits, saved);
    } catch (_) {}
  }

  function _saveDisplayLimits() {
    localStorage.setItem("nm-display-limits", JSON.stringify(_displayLimits));
  }

  function _getSavedOrder() {
    try { return JSON.parse(localStorage.getItem("nm-card-order") || "null"); }
    catch (_) { return null; }
  }

  function _saveOrder() {
    if (!_container) return;
    const ids = Array.from(_container.querySelectorAll("[data-card-id]"))
      .map(c => c.dataset.cardId);
    localStorage.setItem("nm-card-order", JSON.stringify(ids));
  }

  // Reorder DOM children to match saved order.
  // Default (no saved order): dynamic cards first, static cards last.
  function _applyOrder() {
    if (!_container) return;

    const cards = new Map();
    _container.querySelectorAll("[data-card-id]").forEach(c => {
      cards.set(c.dataset.cardId, c);
    });

    const savedOrder = _getSavedOrder();
    if (!savedOrder) {
      const dynamics = [...cards.values()].filter(c => !STATIC_IDS.includes(c.dataset.cardId));
      const statics  = STATIC_IDS.map(id => cards.get(id)).filter(Boolean);
      [...dynamics, ...statics].forEach(c => _container.appendChild(c));
      return;
    }

    for (const id of savedOrder) {
      const card = cards.get(id);
      if (card) _container.appendChild(card);
    }
    // Append anything not captured in the saved order
    for (const [, card] of cards) {
      if (!savedOrder.includes(card.dataset.cardId)) _container.appendChild(card);
    }
  }

  // ── Render ────────────────────────────────────────────────────

  function _render(lists) {
    if (!_container) return;

    // Cache the latest data from server
    for (const list of lists) _cachedLists.set(list.id, list);

    // Remove only dynamic cards; static cards (hops left, pkt breakdown) stay
    _container.querySelectorAll(".nm-list-card--dynamic").forEach(c => c.remove());

    for (const list of lists) {
      const card = _buildCard(list);
      card.dataset.cardId = list.id;
      card.classList.add("nm-list-card--dynamic");
      _container.appendChild(card);
    }

    _applyOrder();
  }

  // ── Card builder ──────────────────────────────────────────────

  function _buildCard(list) {
    const card = document.createElement("div");
    card.className = "nm-list-card";

    const title = document.createElement("div");
    title.className = "nm-list-title nm-list-title--draggable";
    title.textContent = list.title;
    card.appendChild(title);

    // Determine how many rows to display
    // list.limit = configured default from server; fall back to node count if absent
    const limit = _displayLimits[list.id] ?? list.limit ?? list.nodes.length;
    const nodes = list.nodes.slice(0, Math.max(0, Math.min(limit, list.nodes.length)));

    if (nodes.length === 0) {
      const empty = document.createElement("div");
      empty.className = "nm-list-empty";
      empty.style.cssText = "padding:4px 8px;color:var(--text-dim);font-size:11px";
      empty.textContent = "No data";
      card.appendChild(empty);
    } else {
      for (const node of nodes) card.appendChild(_buildRow(node, list.id));
    }

    // Resize handle at the bottom
    const handle = document.createElement("div");
    handle.className = "nm-list-resize-handle";
    card.appendChild(handle);

    _attachResizeDrag(handle, list);

    return card;
  }

  // ── Resize drag (bottom handle) ───────────────────────────────

  function _attachResizeDrag(handle, list) {
    const startDrag = (startY) => {
      const startLimit = _displayLimits[list.id] ?? list.limit ?? list.nodes.length;

      const onMove = (currentY) => {
        const dy     = currentY - startY;
        const delta  = Math.round(dy / ROW_HEIGHT);
        const newLim = Math.max(1, Math.min(10, startLimit + delta));
        if (_displayLimits[list.id] !== newLim) {
          _displayLimits[list.id] = newLim;
          _saveDisplayLimits();
          const card = handle.closest(".nm-list-card");
          if (card) _updateCardRows(card, list, newLim);
        }
      };

      return onMove;
    };

    handle.addEventListener("mousedown", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const onMove = startDrag(e.clientY);
      const move = (e2) => onMove(e2.clientY);
      const up   = () => {
        document.removeEventListener("mousemove", move);
        document.removeEventListener("mouseup", up);
      };
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", up);
    });

    handle.addEventListener("touchstart", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const onMove = startDrag(e.touches[0].clientY);
      const move = (e2) => onMove(e2.touches[0].clientY);
      const up   = () => {
        document.removeEventListener("touchmove", move);
        document.removeEventListener("touchend", up);
      };
      document.addEventListener("touchmove", move, { passive: false });
      document.addEventListener("touchend", up);
    }, { passive: false });
  }

  function _updateCardRows(card, list, limit) {
    card.querySelectorAll(".nm-list-row, .nm-list-empty").forEach(r => r.remove());
    const handle = card.querySelector(".nm-list-resize-handle");
    const nodes  = list.nodes.slice(0, Math.max(0, Math.min(limit, list.nodes.length)));

    if (nodes.length === 0) {
      const empty = document.createElement("div");
      empty.className = "nm-list-empty";
      empty.style.cssText = "padding:4px 8px;color:var(--text-dim);font-size:11px";
      empty.textContent = "No data";
      card.insertBefore(empty, handle || null);
    } else {
      const frag = document.createDocumentFragment();
      for (const node of nodes) frag.appendChild(_buildRow(node, list.id));
      card.insertBefore(frag, handle || null);
    }
  }

  // ── Card reorder drag (title bar) ─────────────────────────────

  function _initCardDragging() {
    if (!_container) return;
    let _dragSrc    = null;
    let _isDragging = false;
    let _startY     = 0;

    const _onMove = (clientX, clientY) => {
      if (!_isDragging && Math.abs(clientY - _startY) > 5) {
        _isDragging = true;
        _dragSrc.classList.add("nm-card-dragging");
      }
      if (!_isDragging) return;

      _dragSrc.style.visibility = "hidden";
      const below  = document.elementFromPoint(clientX, clientY);
      _dragSrc.style.visibility = "";

      const target = below?.closest(".nm-list-card");
      if (!target || target === _dragSrc || target.closest("#nm-lists-container") !== _container) return;

      const rect = target.getBoundingClientRect();
      if (clientY < rect.top + rect.height / 2) {
        _container.insertBefore(_dragSrc, target);
      } else {
        _container.insertBefore(_dragSrc, target.nextSibling);
      }
    };

    const _onEnd = () => {
      if (_dragSrc) {
        _dragSrc.classList.remove("nm-card-dragging");
        if (_isDragging) _saveOrder();
      }
      _dragSrc    = null;
      _isDragging = false;
    };

    // Mouse
    _container.addEventListener("mousedown", (e) => {
      if (!e.target.closest(".nm-list-title--draggable")) return;
      const card = e.target.closest(".nm-list-card");
      if (!card) return;
      e.preventDefault();
      _dragSrc = card;
      _startY  = e.clientY;
      _isDragging = false;

      const move = (e2) => _onMove(e2.clientX, e2.clientY);
      const up   = () => {
        document.removeEventListener("mousemove", move);
        document.removeEventListener("mouseup", up);
        _onEnd();
      };
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", up);
    });

    // Touch
    _container.addEventListener("touchstart", (e) => {
      if (!e.target.closest(".nm-list-title--draggable")) return;
      const card = e.target.closest(".nm-list-card");
      if (!card) return;
      e.preventDefault();
      _dragSrc = card;
      _startY  = e.touches[0].clientY;
      _isDragging = false;

      const move = (e2) => {
        e2.preventDefault();
        _onMove(e2.touches[0].clientX, e2.touches[0].clientY);
      };
      const up = () => {
        document.removeEventListener("touchmove", move);
        document.removeEventListener("touchend", up);
        _onEnd();
      };
      document.addEventListener("touchmove", move, { passive: false });
      document.addEventListener("touchend", up);
    }, { passive: false });
  }

  // ── Row builder ───────────────────────────────────────────────

  function _buildRow(node, listId) {
    const row = document.createElement("div");
    row.className = "nm-list-row";
    row.style.cursor = "pointer";
    const _rowColor = (listId === "last_heard" && node.last_portnum)
      ? _portnumColor(node.last_portnum) : null;

    const shortName = node.short_name || node.node_id_hex || "—";
    const longName  = node.long_name  || node.node_id_hex || "—";
    const combined  = `${shortName} - ${longName}`;

    const emojiEl = document.createElement("span");
    emojiEl.className = "nm-role-icon";
    emojiEl.textContent = _emojiForRole(node.role);
    if (_rowColor) { emojiEl.style.color = _rowColor; emojiEl.style.filter = "brightness(2.2)"; }
    row.appendChild(emojiEl);

    const nameEl = document.createElement("span");
    nameEl.className = "nm-node-name";
    nameEl.dataset.short    = shortName;
    nameEl.dataset.long     = longName;
    nameEl.dataset.combined = combined;
    nameEl.dataset.showing  = "auto";
    nameEl.textContent = _wideMode ? combined : shortName;
    if (_rowColor) { nameEl.style.color = _rowColor; nameEl.style.filter = "brightness(2.2)"; }
    row.appendChild(nameEl);

    const metaEl = document.createElement("span");
    metaEl.className = "nm-node-meta";
    metaEl.textContent = _metaValue(node, listId);
    if (_rowColor) { metaEl.style.color = _rowColor; metaEl.style.filter = "brightness(2.2)"; }
    row.appendChild(metaEl);

    row.addEventListener("click", () => {
      if (NMMap.hasMarker(node.node_id)) {
        NMMap.flyToNode(node.node_id);
      } else {
        _showNodePopup(node, row);
      }
    });

    return row;
  }

  // ── No-marker node popup ─────────────────────────────────────

  function _showNodePopup(node, rowEl) {
    const existing = document.getElementById("nm-node-info-popup");
    if (existing) existing.remove();

    const popup = document.createElement("div");
    popup.id = "nm-node-info-popup";
    popup.className = "nm-node-info-popup";
    popup.innerHTML =
      `<div class="nm-node-info-close">&#x2715;</div>${_buildNodeInfoHtml(node)}`;
    document.body.appendChild(popup);

    const rowRect   = rowEl.getBoundingClientRect();
    const panelEl   = document.getElementById("nm-right-panel");
    const panelRect = panelEl ? panelEl.getBoundingClientRect() : rowRect;
    const popW = popup.offsetWidth;
    const popH = popup.offsetHeight;
    let top  = rowRect.top;
    let left = panelRect.left - popW - 8;
    if (left < 8) left = Math.min(8, panelRect.right + 8);
    if (top + popH > window.innerHeight - 8) top = window.innerHeight - popH - 8;
    if (top < 8) top = 8;
    popup.style.top  = `${top}px`;
    popup.style.left = `${left}px`;

    popup.querySelector(".nm-node-info-close").addEventListener("click", (e) => {
      e.stopPropagation();
      popup.remove();
    });

    setTimeout(() => {
      const dismiss = (e) => {
        if (!popup.contains(e.target)) {
          popup.remove();
          document.removeEventListener("click", dismiss, true);
        }
      };
      document.addEventListener("click", dismiss, true);
    }, 0);
  }

  function _buildNodeInfoHtml(node) {
    const ts      = node.last_heard  ? new Date(node.last_heard  * 1000).toLocaleString() : "—";
    const tsFirst = node.first_seen  ? new Date(node.first_seen  * 1000).toLocaleString() : "—";
    const distMi  = node.distance_km != null
      ? `${(node.distance_km * 0.621371).toFixed(1)} mi` : "—";
    const rows = [
      ["ID",          node.node_id_hex       || "—"],
      ["Short",       node.short_name        || "—"],
      ["Long",        node.long_name         || "—"],
      ["HW",          node.hardware_model    || "—"],
      ["Role",        node.role              || "—"],
      ["Distance",    distMi],
      ["RSSI",        node.rssi        != null ? `${node.rssi} dBm`  : "—"],
      ["SNR",         node.snr         != null ? `${node.snr} dB`    : "—"],
      ["Battery",     node.battery_level != null ? `${node.battery_level}%` : "—"],
      ["Packets",     node.total_packets ?? "—"],
      ["Last heard",  ts],
      ["First heard", tsFirst],
    ];
    const trs = rows.map(([k, v]) =>
      `<tr><td style="color:#888;padding-right:8px;white-space:nowrap">${k}</td><td>${v}</td></tr>`
    ).join("");
    return `<table style="font-size:11px;font-family:monospace;line-height:1.5">${trs}</table>`;
  }

  // ── Width responsiveness ──────────────────────────────────────

  function checkWidth() {
    const panel = document.getElementById("nm-right-panel");
    if (!panel) return;
    const wide = panel.offsetWidth >= WIDE_THRESHOLD;
    if (wide === _wideMode) return;
    _wideMode = wide;
    document.querySelectorAll("#nm-lists-container .nm-node-name").forEach(el => {
      if (el.dataset.showing === "click") return;
      el.textContent = _wideMode ? el.dataset.combined : el.dataset.short;
    });
  }

  // ── Meta value formatter ──────────────────────────────────────

  function _metaValue(node, listId) {
    switch (listId) {
      case "last_heard":
        return node.last_heard ? _ageStr(node.last_heard) : "—";
      case "nearby":
        return node.distance_km != null
          ? `${(node.distance_km * 0.621371).toFixed(1)} mi`
          : "no GPS";
      case "big_sig":
        if (node.rssi == null) return "—";
        return node.snr != null ? `${node.rssi}/${node.snr}` : `${node.rssi} dBm`;
      case "most_pkts":
        return `${node.total_packets} pkts`;
      case "msgs_sent":
        return `${node.sent_count} sent`;
      case "msgs_rcvd":
        return `${node.received_count} rcvd`;
      case "dead_end":
        return `${node.dead_end_count} pkts`;
      default:
        return "";
    }
  }

  function _ageStr(lastHeard) {
    const sec = Math.floor(Date.now() / 1000) - lastHeard;
    if (sec < 60)    return `${sec}s ago`;
    if (sec < 3600)  return `${Math.floor(sec / 60)}m ago`;
    if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
    return `${Math.floor(sec / 86400)}d ago`;
  }

  return { init, checkWidth };
})();
