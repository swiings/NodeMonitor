/**
 * telemetry.js — Telemetry list rendering.
 *
 * Listens for:
 *   "nm:lists-updated" — re-renders all configured telemetry list panels
 *
 * Row behavior:
 *   - Shows short name by default; click toggles to long name
 *   - Click also highlights the node's marker on the map
 */

"use strict";

const NMTelemetry = (() => {
  // Width threshold (px) at which the right panel is wide enough to show
  // "Short - Long Name" instead of just "Short". Calculated from monospace
  // 12px font: ~7.2px/char × (4 short + 3 " - " + 16 long avg + 10 meta pad) = ~237px
  const WIDE_THRESHOLD = 240;
  let _wideMode = false;

  const _ROLE_INT_MAP = {
    "0": "CLIENT", "1": "CLIENT_MUTE", "2": "ROUTER",
    "3": "ROUTER_CLIENT", "4": "CLIENT_HIDDEN", "5": "LOST_AND_FOUND",
    "6": "CLIENT_TRACKER", "7": "SENSOR", "8": "TAK", "9": "CLIENT_HIDDEN",
    "10": "LOST_AND_FOUND_TRACKER", "11": "SENSOR_MQTT", "12": "REPEATER",
    "13": "ROUTER_LATE",
  };

  function _emojiForRole(role) {
    const r = (_ROLE_INT_MAP[String(role ?? "CLIENT")] || String(role ?? "CLIENT")).toUpperCase();
    if (r === "ROUTER" || r === "ROUTER_CLIENT" || r === "ROUTER_LATE" || r === "REPEATER") return "📡";
    return "📱";
  }

  function init() {
    document.addEventListener("nm:lists-updated", (e) => _render(e.detail.lists));
  }

  function _render(lists) {
    const container = document.getElementById("nm-lists-container");
    if (!container) return;
    container.innerHTML = "";
    for (const list of lists) {
      container.appendChild(_buildCard(list));
    }
  }

  function _buildCard(list) {
    const card = document.createElement("div");
    card.className = "nm-list-card";

    const title = document.createElement("div");
    title.className = "nm-list-title";
    title.textContent = list.title;
    card.appendChild(title);

    for (const node of list.nodes) {
      card.appendChild(_buildRow(node, list.id));
    }

    if (list.nodes.length === 0) {
      const empty = document.createElement("div");
      empty.style.cssText = "padding:4px 8px;color:var(--text-dim);font-size:11px";
      empty.textContent = "No data";
      card.appendChild(empty);
    }

    return card;
  }

  function _buildRow(node, listId) {
    const row = document.createElement("div");
    row.className = "nm-list-row";
    row.style.cursor = "pointer";

    const shortName = node.short_name || node.node_id_hex || "—";
    const longName  = node.long_name  || node.node_id_hex || "—";
    const combined  = `${shortName} - ${longName}`;

    // Column 1: role emoji + name
    const emojiEl = document.createElement("span");
    emojiEl.className = "nm-role-icon";
    emojiEl.textContent = _emojiForRole(node.role);
    row.appendChild(emojiEl);

    const nameEl = document.createElement("span");
    nameEl.className = "nm-node-name";
    nameEl.dataset.short    = shortName;
    nameEl.dataset.long     = longName;
    nameEl.dataset.combined = combined;
    nameEl.dataset.showing  = "auto";  // "auto" = driven by panel width; "click" = user toggled
    nameEl.textContent = _wideMode ? combined : shortName;
    row.appendChild(nameEl);

    // Column 2: context-sensitive metric
    const metaEl = document.createElement("span");
    metaEl.className = "nm-node-meta";
    metaEl.textContent = _metaValue(node, listId);
    row.appendChild(metaEl);

    // Click: only active in narrow mode (showing just short name)
    // Temporarily shows long name for 2s then reverts, also highlights map marker
    row.addEventListener("click", () => {
      if (_wideMode) {
        // In wide mode the combined name is already visible — just highlight
        NMMap.highlightNode(node.node_id);
        return;
      }
      if (nameEl.dataset.showing === "click") return;  // already in click-toggled state
      nameEl.dataset.showing = "click";
      nameEl.textContent = longName;
      NMMap.highlightNode(node.node_id);
      setTimeout(() => {
        if (nameEl.dataset.showing === "click") {
          nameEl.dataset.showing = "auto";
          nameEl.textContent = _wideMode ? combined : shortName;
        }
      }, 2000);
    });

    return row;
  }

  // Called by app.js whenever the right panel width changes
  function checkWidth() {
    const panel = document.getElementById("nm-right-panel");
    if (!panel) return;
    const wide = panel.offsetWidth >= WIDE_THRESHOLD;
    if (wide === _wideMode) return;
    _wideMode = wide;
    // Update all name elements currently in the DOM
    document.querySelectorAll("#nm-lists-container .nm-node-name").forEach(el => {
      if (el.dataset.showing === "click") return;  // don't interrupt a click-toggle
      el.textContent = _wideMode ? el.dataset.combined : el.dataset.short;
    });
  }

  function _metaValue(node, listId) {
    switch (listId) {
      case "last_heard":
        return node.last_heard ? _ageStr(node.last_heard) : "—";
      case "nearby":
        return node.distance_km != null
          ? `${(node.distance_km * 0.621371).toFixed(1)} mi`
          : "no GPS";
      case "big_sig":
        return node.rssi != null ? `${node.rssi} dBm` : "—";
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
