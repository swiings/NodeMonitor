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
  const STATIC_IDS = ["hops_left", "pkt_breakdown", "hw_breakdown"];

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

    const shortName = node.short_name || node.node_id_hex || "—";
    const longName  = node.long_name  || node.node_id_hex || "—";
    const combined  = `${shortName} - ${longName}`;

    const emojiEl = document.createElement("span");
    emojiEl.className = "nm-role-icon";
    emojiEl.textContent = _emojiForRole(node.role);
    row.appendChild(emojiEl);

    const nameEl = document.createElement("span");
    nameEl.className = "nm-node-name";
    nameEl.dataset.short    = shortName;
    nameEl.dataset.long     = longName;
    nameEl.dataset.combined = combined;
    nameEl.dataset.showing  = "auto";
    nameEl.textContent = _wideMode ? combined : shortName;
    row.appendChild(nameEl);

    const metaEl = document.createElement("span");
    metaEl.className = "nm-node-meta";
    metaEl.textContent = _metaValue(node, listId);
    row.appendChild(metaEl);

    row.addEventListener("click", () => {
      if (_wideMode) {
        NMMap.flyToNode(node.node_id);
        return;
      }
      if (nameEl.dataset.showing === "click") return;
      nameEl.dataset.showing = "click";
      nameEl.textContent = longName;
      NMMap.flyToNode(node.node_id);
      setTimeout(() => {
        if (nameEl.dataset.showing === "click") {
          nameEl.dataset.showing = "auto";
          nameEl.textContent = _wideMode ? combined : shortName;
        }
      }, 2000);
    });

    return row;
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
