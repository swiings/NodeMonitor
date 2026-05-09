/**
 * gauges.js — Indicator gauges across the full page width.
 *
 * Listens for:
 *   "nm:gauges-updated" — updates all gauge displays
 *
 * Closest / Farthest gauges are tappable: tapping flies the map to that node.
 */

"use strict";

const NMGauges = (() => {
  let _closestNodeId  = null;
  let _farthestNodeId = null;

  // ── Generic chart-card resize ─────────────────────────────────
  const CHART_ROW_HEIGHT = 22;

  // State per resizable card: cardId → { allRows, limit, storageKey, renderFn }
  const _chartState = {};

  function _initChartResize(cardId, storageKey) {
    const state = { allRows: [], limit: null, storageKey, renderFn: null };
    _chartState[cardId] = state;
    try { state.limit = JSON.parse(localStorage.getItem(storageKey)); } catch (_) {}

    const card = document.getElementById(cardId);
    if (!card) return;
    const handle = document.createElement("div");
    handle.className = "nm-list-resize-handle";
    card.appendChild(handle);

    const startDrag = (startY) => {
      const startLimit = state.limit ?? state.allRows.length;
      return (currentY) => {
        const delta  = Math.round((currentY - startY) / CHART_ROW_HEIGHT);
        const newLim = Math.max(1, Math.min(20, startLimit + delta));
        if (state.limit !== newLim) {
          state.limit = newLim;
          localStorage.setItem(storageKey, JSON.stringify(newLim));
          if (state.renderFn) state.renderFn();
        }
      };
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

  function _slicedRows(cardId) {
    const s = _chartState[cardId];
    return s.limit != null ? s.allRows.slice(0, s.limit) : s.allRows;
  }

  function init() {
    _initChartResize("nm-pkt-breakdown",  "nm-chart-limit-pkts");
    _initChartResize("nm-hw-breakdown",   "nm-chart-limit-hw");
    _initChartResize("nm-role-breakdown", "nm-chart-limit-roles");
    _chartState["nm-pkt-breakdown"].renderFn  = _renderPktBreakdown;
    _chartState["nm-hw-breakdown"].renderFn   = _renderHwBreakdown;
    _chartState["nm-role-breakdown"].renderFn = _renderRoleBreakdown;
    document.addEventListener("nm:gauges-updated",    (e) => _update(e.detail));
    document.addEventListener("nm:breakdown-updated", (e) => _updateBreakdown(e.detail.breakdown || []));

    document.getElementById("gauge-closest").addEventListener("click", () => {
      if (_closestNodeId != null) NMMap.flyToNode(_closestNodeId);
    });
    document.getElementById("gauge-farthest").addEventListener("click", () => {
      if (_farthestNodeId != null) NMMap.flyToNode(_farthestNodeId);
    });
  }

  // Role → emoji (mirrors map.js mapping)
  const _GAUGE_ROLE_INT_MAP = {
    "0": "CLIENT", "1": "CLIENT_MUTE", "2": "ROUTER",
    "3": "ROUTER_CLIENT", "4": "CLIENT_HIDDEN", "5": "LOST_AND_FOUND",
    "6": "SENSOR", "7": "TAK", "8": "TAK_TRACKER", "9": "REPEATER", "10": "TRACKER",
  };
  function _roleEmoji(role) {
    const r = (_GAUGE_ROLE_INT_MAP[String(role ?? "0")] || String(role || "CLIENT")).toUpperCase();
    if (r === "ROUTER" || r === "ROUTER_CLIENT" || r === "ROUTER_LATE" || r === "REPEATER") return "📡";
    if (r === "TRACKER" || r === "TAK" || r === "TAK_TRACKER" || r === "LOST_AND_FOUND")    return "🐾";
    if (r === "SENSOR")  return "📊";
    if (r === "CLIENT_MUTE")  return "😎";
    if (r === "CLIENT_HIDDEN")  return "👻";
    if (r === "CLIENT_BASE")  return "🏡";
    if (r === "CLIENT" || r.startsWith("CLIENT_")) return "📱";
    return "👻";
  }

  function _update(data) {
    _set("g-direct", data.direct_count ?? "—");
    _set("g-active", data.active_count  ?? "—");
    _set("g-total",  data.total_count   ?? "—");
    _set("g-ppm",    data.packets_per_minute != null ? data.packets_per_minute.toFixed(1) : "—");
    _set("g-pph",    data.packets_per_hour   ?? "—");
    _set("g-session-pkts", data.session_packets != null ? _fmt(data.session_packets) : "—");
    _set("g-elapsed", data.session_start_ts ? _elapsed(data.session_start_ts) : "—");

    if (data.closest) {
      _closestNodeId = data.closest.node_id;
      _set("g-closest-name", `${_roleEmoji(data.closest.role)} ${data.closest.short_name}`);
      _set("g-closest-dist", `${data.closest.distance_mi} mi`);
    } else {
      _closestNodeId = null;
      _set("g-closest-name", "—");
      _set("g-closest-dist", "");
    }

    if (data.farthest) {
      _farthestNodeId = data.farthest.node_id;
      _set("g-farthest-name", `${_roleEmoji(data.farthest.role)} ${data.farthest.short_name}`);
      _set("g-farthest-dist", `${data.farthest.distance_mi} mi`);
    } else {
      _farthestNodeId = null;
      _set("g-farthest-name", "—");
      _set("g-farthest-dist", "");
    }

    _set("g-router-count", data.router_count ?? "—");
    _set("g-client-count", data.client_count ?? "—");

    // if (data.hop_dist) {
    //   _drawHopPie(data.hop_dist);
    // }
    if (data.hop_dist_raw) {
      _updateHopDistRaw(data.hop_dist_raw);
    }
    if (data.hardware_breakdown) {
      _updateHardwareBreakdown(data.hardware_breakdown);
    }
    if (data.role_breakdown) {
      _updateRoleBreakdown(data.role_breakdown);
    }
  }

  // Hops-left colors: warm earth/bronze gradient (close=lighter copper, far=dark slate).
  // Distinct from the activity bar palette (which uses violet/cobalt/forest/gold/crimson).
  const _HOP_COLORS = {
    "4+": "#6b9e7a",   // muted sage — most hops remaining, closest to sender
    "3":  "#7a7a3a",   // olive
    "2":  "#8a6020",   // dark bronze
    "1":  "#7a3818",   // deep rust
    "0":  "#710b00",   // dark red-slate — fewest hops, most traversed
  };

 /*  function _drawHopPie(dist) {
    const canvas = document.getElementById("gauge-hops-canvas");
    if (!canvas) return;
    const gauge  = document.getElementById("gauge-hops");
    if (!gauge) return;

    // Size canvas to fit the gauge, leaving room for the caption below
    const captionH = 20;
    const size = Math.min(gauge.clientWidth, gauge.clientHeight - captionH) - 8;
    if (size < 10) return;
    canvas.width  = size;
    canvas.height = size;

    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, size, size);

    const buckets = ["4+", "3", "2", "1", "0"];
    const total = buckets.reduce((s, k) => s + (dist[k] || 0), 0);
    if (total === 0) return;

    const cx = size / 2, cy = size / 2, r = size / 2 - 1;
    let angle = -Math.PI / 2;  // start at top

    const slices = [];
    for (const k of buckets) {
      const count = dist[k] || 0;
      if (count === 0) continue;
      const sweep = (count / total) * 2 * Math.PI;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, r, angle, angle + sweep);
      ctx.closePath();
      ctx.fillStyle = _HOP_COLORS[k];
      ctx.fill();
      slices.push({ k, count, midAngle: angle + sweep / 2 });
      angle += sweep;
    }

    // Labels — only draw if slice is large enough to fit text
    const fontSize = Math.max(9, Math.round(size * 0.14));
    ctx.font = `bold ${fontSize}px "Courier New", monospace`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    const minSlice = 0.25;  // radians — skip tiny slices
    for (const { k, count, midAngle } of slices) {
      const sweep = (count / total) * 2 * Math.PI;
      if (sweep < minSlice) continue;
      const lx = cx + Math.cos(midAngle) * r * 0.62;
      const ly = cy + Math.sin(midAngle) * r * 0.62;
      ctx.fillStyle = "rgba(0,0,0,0.75)";
      ctx.fillText(k, lx + 1, ly + 1);
      ctx.fillStyle = "#ffffff";
      ctx.fillText(k, lx, ly);
    }
  } */

  // Dynamic hop-left list from raw per-value data (replaces static bucketed table)
  function _updateHopDistRaw(distRaw) {
    const table = document.getElementById("nm-hop-dist-table");
    if (!table) return;

    // Separate the "?" (null hop_limit) entry from numeric entries
    const nullCount = distRaw["?"] || 0;
    const numericEntries = Object.entries(distRaw)
      .filter(([k]) => k !== "?")
      .map(([k, v]) => ({ hopLimit: parseInt(k), count: v }))
      .filter(e => e.count > 0)
      .sort((a, b) => b.hopLimit - a.hopLimit);

    if (numericEntries.length === 0 && !nullCount) {
      table.innerHTML = "";
      return;
    }

    const allCounts = [...numericEntries.map(e => e.count), nullCount].filter(Boolean);
    const max = Math.max(1, ...allCounts);

    const numericRows = numericEntries.map(e => {
      const pct   = Math.round((e.count / max) * 100);
      const color = e.hopLimit >= 4 ? _HOP_COLORS["4+"]
                  : _HOP_COLORS[String(e.hopLimit)] || _HOP_COLORS["0"];
      return `<tr>
        <td class="nm-hop-label">${e.hopLimit}</td>
        <td class="nm-hop-bar-cell"><div class="nm-hop-bar" style="width:${pct}%;background:${color}"></div></td>
        <td class="nm-hop-count">${e.count}</td>
      </tr>`;
    }).join("");

    // "?" row: packets with no hop_limit field (protobuf omits 0-value fields,
    // so many 0-hops-remaining packets land here)
    const nullRow = nullCount ? (() => {
      const pct = Math.round((nullCount / max) * 100);
      return `<tr title="hop_limit not reported — includes packets where hop_limit=0 was omitted by protobuf">
        <td class="nm-hop-label" style="color:var(--text-dim)">0</td>
        <td class="nm-hop-bar-cell"><div class="nm-hop-bar" style="width:${pct}%;background:${_HOP_COLORS["0"]};"></div></td>
        <td class="nm-hop-count" style="color:var(--text-dim)">${nullCount}</td>
      </tr>`;
    })() : "";

    table.innerHTML = numericRows + nullRow;
  }

  function _set(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  // Compact number: 1234 → "1.2K", 1234567 → "1.2M"
  function _fmt(n) {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 10_000)    return `${(n / 1_000).toFixed(0)}K`;
    if (n >= 1_000)     return `${(n / 1_000).toFixed(1)}K`;
    return String(n);
  }

  // Packet Breakdown list — colors per portnum
  const _PKT_COLORS = {
    "TEXT_MESSAGE_APP": "var(--red)",
    "POSITION_APP":     "var(--green)",
    "TELEMETRY_APP":    "var(--accent)",
    "NODEINFO_APP":     "var(--purple)",
    "UNKNOWN":          "var(--amber)",
  };

  function _pktColor(portnum) {
    return _PKT_COLORS[portnum] || "var(--text-dim)";
  }

  function _pktLabel(portnum) {
    const labels = {
      "TEXT_MESSAGE_APP": "TEXT MSG",
      "POSITION_APP":     "POSITION",
      "TELEMETRY_APP":    "TELEMETRY",
      "NODEINFO_APP":     "NODE INFO",
      "UNKNOWN":          "ENCRYPTED",
    };
    return labels[portnum] || portnum.replace(/_APP$/, "").replace(/_/g, " ");
  }

  function _updateBreakdown(rows) {
    const state = _chartState["nm-pkt-breakdown"];
    if (!state) return;
    state.allRows = rows || [];
    _renderPktBreakdown();
  }

  function _renderPktBreakdown() {
    const table = document.getElementById("nm-pkt-breakdown-table");
    if (!table) return;
    const rows = _slicedRows("nm-pkt-breakdown");
    if (rows.length === 0) {
      table.innerHTML = `<tr><td colspan="3" class="nm-hop-label" style="color:var(--text-dim)">No data</td></tr>`;
      return;
    }
    const maxCount = Math.max(1, ...rows.map(r => r.count));
    table.innerHTML = rows.map(r => {
      const pct = Math.round((r.count / maxCount) * 100);
      const color = _pktColor(r.portnum);
      const label = _pktLabel(r.portnum);
      return `<tr>
        <td class="nm-hop-label" style="min-width:48px;color:${color}">${label}</td>
        <td class="nm-hop-bar-cell"><div class="nm-hop-bar" style="width:${pct}%;background:${color};opacity:0.8"></div></td>
        <td class="nm-hop-count">${r.count}</td>
      </tr>`;
    }).join("");
  }

  // Hardware model breakdown
  function _updateHardwareBreakdown(rows) {
    const state = _chartState["nm-hw-breakdown"];
    if (!state) return;
    state.allRows = rows || [];
    _renderHwBreakdown();
  }

  function _renderHwBreakdown() {
    const table = document.getElementById("nm-hw-breakdown-table");
    if (!table) return;
    const rows = _slicedRows("nm-hw-breakdown");
    if (rows.length === 0) {
      table.innerHTML = `<tr><td colspan="3" class="nm-hop-label" style="color:var(--text-dim)">No data</td></tr>`;
      return;
    }
    const maxCount = Math.max(1, ...rows.map(r => r.count));
    const color = "var(--accent)";
    table.innerHTML = rows.map(r => {
      const pct   = Math.round((r.count / maxCount) * 100);
      const label = r.hardware_model || "Unknown";
      return `<tr>
        <td class="nm-hop-label" style="min-width:60px;color:${color}">${label}</td>
        <td class="nm-hop-bar-cell"><div class="nm-hop-bar" style="width:${pct}%;background:${color};opacity:0.75"></div></td>
        <td class="nm-hop-count">${r.count}</td>
      </tr>`;
    }).join("");
  }

  // Role breakdown — role label → emoji mapping for display
  const _ROLE_LABELS = {
    "CLIENT":        "📱 CLIENT",
    "CLIENT_MUTE":   "😎 CLIENT_MUTE",
    "CLIENT_HIDDEN": "👻 CLIENT_HIDDEN",
    "CLIENT_BASE":   "🏡 CLIENT_BASE",
    "ROUTER":        "📡 ROUTER",
    "ROUTER_CLIENT": "📡 ROUTER_CLIENT",
    "ROUTER_LATE":   "📡 ROUTER_LATE",
    "REPEATER":      "📡 REPEATER",
    "TRACKER":       "🐾 TRACKER",
    "LOST_AND_FOUND":"🐾 LOST_AND_FOUND",
    "TAK":           "🐾 TAK",
    "TAK_TRACKER":   "🐾 TAK_TRACKER",
    "SENSOR":        "📊 SENSOR",
  };

  function _updateRoleBreakdown(rows) {
    const state = _chartState["nm-role-breakdown"];
    if (!state) return;
    state.allRows = rows || [];
    _renderRoleBreakdown();
  }

  function _renderRoleBreakdown() {
    const table = document.getElementById("nm-role-breakdown-table");
    if (!table) return;
    const rows = _slicedRows("nm-role-breakdown");
    if (rows.length === 0) {
      table.innerHTML = `<tr><td colspan="3" class="nm-hop-label" style="color:var(--text-dim)">No data</td></tr>`;
      return;
    }
    const maxCount = Math.max(1, ...rows.map(r => r.count));
    const color = "var(--purple)";
    table.innerHTML = rows.map(r => {
      const pct   = Math.round((r.count / maxCount) * 100);
      const label = _ROLE_LABELS[r.role] || `👻 ${r.role}`;
      return `<tr>
        <td class="nm-hop-label" style="min-width:180px;color:${color}">${label}</td>
        <td class="nm-hop-bar-cell"><div class="nm-hop-bar" style="width:${pct}%;background:${color};opacity:0.75"></div></td>
        <td class="nm-hop-count">${r.count}</td>
      </tr>`;
    }).join("");
  }

  // Elapsed time since first packet
  function _elapsed(firstTs) {
    const secs = Math.floor(Date.now() / 1000) - firstTs;
    if (secs < 60)    return `${secs}s`;
    const mins = Math.floor(secs / 60);
    if (mins < 60)    return `${mins}m`;
    const hrs  = Math.floor(mins / 60);
    const remM = mins % 60;
    if (hrs < 24)     return remM > 0 ? `${hrs}h ${remM}m` : `${hrs}h`;
    const days = Math.floor(hrs / 24);
    const remH = hrs % 24;
    return remH > 0 ? `${days}d ${remH}h` : `${days}d`;
  }

  return { init };
})();
