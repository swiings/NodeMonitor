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
 *     displayed row count (min 1, max 20). Limit persists in localStorage.
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

  // Matches _PKT_COLORS in gauges.js (same palette as the Packet Breakdown chart bars)
  const _PORTNUM_COLORS = {
    "TEXT_MESSAGE_APP":   "var(--red)",
    "POSITION_APP":       "var(--green)",
    "TELEMETRY_APP":      "var(--accent)",
    "NODEINFO_APP":       "var(--purple)",
    "UNKNOWN_PRIVATE_CH": "#ddaaff",
    "UNKNOWN_DIRECT":     "#c06020",
    "STORE_FORWARD_APP":  "#b0cf31",
    "NEIGHBORINFO_APP":   "#acef8d",
    "RANGE_TEST_APP":     "#c09020",
    "ADMIN_APP":          "#20a3c0",
    "PAXCOUNTER_APP":     "#b389fd",
    "UNKNOWN":            "#75f8f8",  
  };

  function _portnumColor(portnum) {
    return _PORTNUM_COLORS[portnum] || null;
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
    if (r === "VEHICLE") return "🚙";
    if (r === "CLIENT_BASE" ) return "🏡";
    if (r === "CLIENT" ) return "📱";
    if (r === "CLIENT_MUTE" ) return "😶";
    if (r === "CLIENT_HIDDEN" ) return "👻";
    if (r === "ROUTER" || r === "ROUTER_CLIENT" || r === "ROUTER_LATE" || r === "REPEATER") return "📡";
    return "😎";
  }

  // ── Init ──────────────────────────────────────────────────────

  function init() {
    _container = document.getElementById("nm-lists-container");
    _loadDisplayLimits();
    _initCardDragging(_container, "nm-card-order");
    _initCardDragging(
      document.getElementById("nm-charts-container"),
      "nm-charts-order"
    );
    _applyChartsOrder();
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
    try {
      const saved = JSON.parse(localStorage.getItem("nm-card-order") || "null");
      if (!Array.isArray(saved)) return null;
      const normalized = [];
      for (const id of saved) {
        const mapped = id === "lil_sig" ? "big_sig" : id;
        if (!normalized.includes(mapped)) normalized.push(mapped);
      }
      return normalized;
    }
    catch (_) { return null; }
  }

  function _saveOrder() {
    if (!_container) return;
    const ids = Array.from(_container.querySelectorAll("[data-card-id]"))
      .map(c => c.dataset.cardId);
    localStorage.setItem("nm-card-order", JSON.stringify(ids));
  }

  function _applyChartsOrder() {
    const chartsEl = document.getElementById("nm-charts-container");
    if (!chartsEl) return;
    try {
      const saved = JSON.parse(localStorage.getItem("nm-charts-order") || "null");
      if (!Array.isArray(saved)) return;
      const cards = new Map();
      chartsEl.querySelectorAll("[data-card-id]").forEach(c => cards.set(c.dataset.cardId, c));
      const known = ["hops_left", "hop_usage", "pkt_breakdown", "hw_breakdown", "role_breakdown"];
      const order = [];
      for (const id of saved) {
        if (known.includes(id) && !order.includes(id)) order.push(id);
      }
      if (!order.includes("hop_usage")) {
        const hopIdx = order.indexOf("hops_left");
        if (hopIdx >= 0) order.splice(hopIdx + 1, 0, "hop_usage");
      }
      for (const id of known) {
        if (!order.includes(id)) order.push(id);
      }
      for (const id of order) {
        const card = cards.get(id);
        if (card) chartsEl.appendChild(card);
      }
    } catch (_) {}
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

    const byId = new Map(lists.map(list => [list.id, list]));

    for (const list of lists) {
      if (list.id === "lil_sig" && byId.has("big_sig")) {
        continue;
      }

      let card = null;
      if (list.id === "big_sig" && byId.has("lil_sig")) {
        card = _buildSigFlipCard(list, byId.get("lil_sig"));
      } else {
        card = _buildCard(list);
      }

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

    // Scrollable body — height is controlled by the resize handle
    const limit = _displayLimits[list.id] ?? list.limit ?? 5;
    const body = document.createElement("div");
    body.className = "nm-list-body";
    body.style.height = `${Math.max(1, limit) * ROW_HEIGHT}px`;
    _populateListBody(body, list);

    card.appendChild(body);

    // Resize handle at the bottom
    const handle = document.createElement("div");
    handle.className = "nm-list-resize-handle";
    card.appendChild(handle);

    _attachResizeDrag(handle, list);

    return card;
  }

  function _buildSigFlipCard(bigSig, lilSig) {
    const card = document.createElement("div");
    card.className = "nm-list-card nm-list-card--sig-flip";

    const title = document.createElement("div");
    title.className = "nm-list-title nm-list-title--draggable";
    title.textContent = "BIG SIG (CLICK TO FLIP)";
    card.appendChild(title);

    const limit = _displayLimits.big_sig ?? _displayLimits.lil_sig ?? bigSig.limit ?? lilSig.limit ?? 5;
    const body = document.createElement("div");
    body.className = "nm-list-body nm-list-body--sig-flip";
    body.style.height = `${Math.max(1, limit) * ROW_HEIGHT}px`;

    const flipper = document.createElement("div");
    flipper.className = "nm-sig-flip-inner";

    const front = document.createElement("div");
    front.className = "nm-sig-flip-face nm-sig-flip-face--front";
    _populateListBody(front, bigSig);

    const back = document.createElement("div");
    back.className = "nm-sig-flip-face nm-sig-flip-face--back";
    _populateListBody(back, lilSig);

    flipper.appendChild(front);
    flipper.appendChild(back);
    body.appendChild(flipper);

    title.addEventListener("click", () => {
      const flipped = body.classList.toggle("is-flipped");
      title.textContent = flipped ? "LIL SIG (CLICK TO FLIP)" : "BIG SIG (CLICK TO FLIP)";
    });

    card.appendChild(body);

    const handle = document.createElement("div");
    handle.className = "nm-list-resize-handle";
    card.appendChild(handle);
    _attachSigResizeDrag(handle, bigSig, lilSig);

    return card;
  }

  function _populateListBody(body, list) {
    const visibleNodes = _visibleNodes(list.nodes, list.id);
    if (visibleNodes.length === 0) {
      const empty = document.createElement("div");
      empty.className = "nm-list-empty";
      empty.style.cssText = "padding:4px 8px;color:var(--text-dim);font-size:11px";
      empty.textContent = (list.id === "msgs_sent" || list.id === "msgs_rcvd") ? "None" : "No data";
      body.appendChild(empty);
      return;
    }
    for (const node of visibleNodes) body.appendChild(_buildRow(node, list.id));
  }

  // ── Resize drag (bottom handle) ───────────────────────────────

  function _attachResizeDrag(handle, list) {
    const startDrag = (startY) => {
      const startLimit = _displayLimits[list.id] ?? list.limit ?? 5;

      const onMove = (currentY) => {
        const dy     = currentY - startY;
        const delta  = Math.round(dy / ROW_HEIGHT);
        const newLim = Math.max(1, Math.min(20, startLimit + delta));
        if (_displayLimits[list.id] !== newLim) {
          _displayLimits[list.id] = newLim;
          _saveDisplayLimits();
          const card = handle.closest(".nm-list-card");
          const body = card?.querySelector(".nm-list-body");
          if (body) body.style.height = `${newLim * ROW_HEIGHT}px`;
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

  function _attachSigResizeDrag(handle, bigSig, lilSig) {
    const startDrag = (startY) => {
      const startLimit = _displayLimits.big_sig ?? _displayLimits.lil_sig ?? bigSig.limit ?? lilSig.limit ?? 5;

      const onMove = (currentY) => {
        const dy = currentY - startY;
        const delta = Math.round(dy / ROW_HEIGHT);
        const newLim = Math.max(1, Math.min(20, startLimit + delta));
        if (_displayLimits.big_sig !== newLim || _displayLimits.lil_sig !== newLim) {
          _displayLimits.big_sig = newLim;
          _displayLimits.lil_sig = newLim;
          _saveDisplayLimits();
          const card = handle.closest(".nm-list-card");
          const body = card?.querySelector(".nm-list-body");
          if (body) body.style.height = `${newLim * ROW_HEIGHT}px`;
        }
      };

      return onMove;
    };

    handle.addEventListener("mousedown", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const onMove = startDrag(e.clientY);
      const move = (e2) => onMove(e2.clientY);
      const up = () => {
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
      const up = () => {
        document.removeEventListener("touchmove", move);
        document.removeEventListener("touchend", up);
      };
      document.addEventListener("touchmove", move, { passive: false });
      document.addEventListener("touchend", up);
    }, { passive: false });
  }

  function _updateCardRows(card, list, limit) {
    const body = card.querySelector(".nm-list-body");
    if (!body) return;
    body.style.height = `${Math.max(1, limit) * ROW_HEIGHT}px`;
    body.innerHTML = "";
    _populateListBody(body, list);
  }

  // ── Card reorder drag (title bar) ─────────────────────────────

  function _initCardDragging(container, orderKey) {
    if (!container) return;
    let _dragSrc    = null;
    let _isDragging = false;
    let _startY     = 0;

    const _saveContainerOrder = () => {
      const ids = Array.from(container.querySelectorAll("[data-card-id]"))
        .map(c => c.dataset.cardId);
      localStorage.setItem(orderKey, JSON.stringify(ids));
    };

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
      if (!target || target === _dragSrc || target.parentElement !== container) return;

      const rect = target.getBoundingClientRect();
      if (clientY < rect.top + rect.height / 2) {
        container.insertBefore(_dragSrc, target);
      } else {
        container.insertBefore(_dragSrc, target.nextSibling);
      }
    };

    const _onEnd = () => {
      if (_dragSrc) {
        _dragSrc.classList.remove("nm-card-dragging");
        if (_isDragging) _saveContainerOrder();
      }
      _dragSrc    = null;
      _isDragging = false;
    };

    // Mouse
    container.addEventListener("mousedown", (e) => {
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
    container.addEventListener("touchstart", (e) => {
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
    const combined  = shortName === longName ? shortName : `${shortName} - ${longName}`;

    const emojiEl = document.createElement("span");
    emojiEl.className = "nm-role-icon";
    emojiEl.textContent = _emojiForRole(node.role);
    if (_rowColor) emojiEl.style.color = _rowColor;
    row.appendChild(emojiEl);

    const nameEl = document.createElement("span");
    nameEl.className = "nm-node-name";
    nameEl.dataset.short    = shortName;
    nameEl.dataset.long     = longName;
    nameEl.dataset.combined = combined;
    nameEl.dataset.showing  = "auto";
    nameEl.textContent = _wideMode ? combined : shortName;
    if (_rowColor) nameEl.style.color = _rowColor;
    row.appendChild(nameEl);

    const metaEl = document.createElement("span");
    metaEl.className = "nm-node-meta";
    metaEl.textContent = _metaValue(node, listId);
    if (_rowColor) metaEl.style.color = _rowColor;
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

    const trafficBtn = popup.querySelector(".nm-traffic-btn");
    if (trafficBtn) {
      trafficBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        const label = node.short_name || node.node_id_hex || String(node.node_id);
        document.dispatchEvent(new CustomEvent("nm:open-traffic", { detail: {
          by: "node", value: node.node_id, label,
        }}));
        popup.remove();
      });
    }

    const trackBtn = popup.querySelector(".nm-track-btn");
    if (trackBtn) {
      trackBtn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const nodeId = node.node_id;
        try {
          if (typeof NMMap.createAndShowTrack === "function") {
            await NMMap.createAndShowTrack(nodeId);
            popup.remove();
          }
        } catch (_) {}
      });
    }

    const deleteBtn = popup.querySelector(".nm-node-delete-btn");
    if (deleteBtn) {
      deleteBtn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const label = node.short_name || node.node_id_hex || node.node_id;
        if (!confirm(`Delete node ${label} from this session?`)) return;
        const resp = await fetch(`/api/nodes/${node.node_id}`, { method: "DELETE" });
        if (resp.ok) {
          popup.remove();
          document.dispatchEvent(new CustomEvent("nm:request-poll"));
        } else {
          const data = await resp.json().catch(() => ({}));
          alert(data.error || "Delete failed");
        }
      });
    }

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
    if (typeof NMMap?.buildNodePopupHtml === "function") {
      return NMMap.buildNodePopupHtml(node);
    }

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
      ["Packet Source", node.packet_source    || "—"],
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
    return `<table style="font-size:11px;font-family:monospace;line-height:1.5">${trs}</table>`
      + `<div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px">`
      + `<button class="nm-text-btn nm-traffic-btn" data-node-id="${node.node_id}" style="font-size:10px;padding:2px 8px">See Traffic</button>`
      + `<button class="nm-btn-danger nm-node-delete-btn" data-node-id="${node.node_id}" style="font-size:10px;padding:2px 8px">Delete Node</button>`
      + `</div>`;
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

  // ── Node filter for count-based lists ────────────────────────

  function _visibleNodes(nodes, listId) {
    if (listId === "msgs_sent") return nodes.filter(n => (n.sent_count || 0) > 0);
    if (listId === "msgs_rcvd") return nodes.filter(n => (n.received_count || 0) > 0);
    return nodes;
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
      case "lil_sig":
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
