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

    const body = card.querySelector(".nm-list-body");
    if (body) {
      body.style.height = `${(state.limit ?? 5) * CHART_ROW_HEIGHT}px`;
    }

    const handle = document.createElement("div");
    handle.className = "nm-list-resize-handle";
    card.appendChild(handle);

    const startDrag = (startY) => {
      const startLimit = state.limit ?? 5;
      return (currentY) => {
        const delta  = Math.round((currentY - startY) / CHART_ROW_HEIGHT);
        const newLim = Math.max(1, Math.min(20, startLimit + delta));
        if (state.limit !== newLim) {
          state.limit = newLim;
          localStorage.setItem(storageKey, JSON.stringify(newLim));
          if (body) body.style.height = `${newLim * CHART_ROW_HEIGHT}px`;
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
    return _chartState[cardId].allRows;
  }

  function init() {
    _initChartResize("nm-hop-dist",       "nm-chart-limit-hops");
    _initChartResize("nm-hop-usage",      "nm-chart-limit-hop-usage");
    _initChartResize("nm-pkt-breakdown",  "nm-chart-limit-pkts");
    _initChartResize("nm-hw-breakdown",   "nm-chart-limit-hw");
    _initChartResize("nm-role-breakdown", "nm-chart-limit-roles");
    _chartState["nm-hop-usage"].renderFn      = _renderHopUsage;
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

    // Delegated click → open traffic window from breakdown rows
    for (const tableId of ["nm-pkt-breakdown-table", "nm-hw-breakdown-table", "nm-role-breakdown-table"]) {
      document.getElementById(tableId)?.addEventListener("click", (e) => {
        const tr = e.target.closest("tr[data-traffic-by]");
        if (!tr) return;
        document.dispatchEvent(new CustomEvent("nm:open-traffic", { detail: {
          by:    tr.dataset.trafficBy,
          value: tr.dataset.trafficValue,
          label: tr.dataset.trafficLabel,
        }}));
      });
    }
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
    if (r === "CLIENT_MUTE")  return "😶";
    if (r === "CLIENT_HIDDEN")  return "👻";
    if (r === "CLIENT_BASE")  return "🏡";
    if (r === "CLIENT" || r.startsWith("CLIENT_")) return "📱";
    return "👻";
  }

  function _hopsLabel(hops_away) {
    if (hops_away == null) return "?";
    if (hops_away === 0)   return "direct";
    return `${hops_away} hop${hops_away === 1 ? "" : "s"}`;
  }

  function _update(data) {
    _set("g-direct", data.direct_count ?? "—");
    _set("g-active", data.active_count  ?? "—");
    _set("g-total",  data.total_count   ?? "—");
    _set("g-ppm",    data.packets_per_minute != null ? data.packets_per_minute.toFixed(1) : "—");
    _set("g-pph",    data.packets_per_hour   ?? "—");
    _set("g-avg-snr",  data.avg_snr_1h  != null ? data.avg_snr_1h.toFixed(1) : "—");
    _set("g-avg-rssi", data.avg_rssi_1h != null ? data.avg_rssi_1h            : "—");
    _set("g-noise-floor", data.noise_floor != null ? data.noise_floor        : "—");
    _set("g-session-pkts", data.session_packets != null ? _fmt(data.session_packets) : "—");
    _set("g-elapsed", data.session_start_ts ? _elapsed(data.session_start_ts) : "—");

    if (data.closest) {
      _closestNodeId = data.closest.node_id;
      _set("g-closest-name", `${_roleEmoji(data.closest.role)} ${data.closest.short_name}`);
      _set("g-closest-text", `${data.closest.distance_mi} mi · ${_hopsLabel(data.closest.hops_away)}`);
    } else {
      _closestNodeId = null;
      _set("g-closest-name", "—");
      _set("g-closest-text", "");
    }

    if (data.farthest) {
      _farthestNodeId = data.farthest.node_id;
      _set("g-farthest-name", `${_roleEmoji(data.farthest.role)} ${data.farthest.short_name}`);
      _set("g-farthest-text", `${data.farthest.distance_mi} mi · ${_hopsLabel(data.farthest.hops_away)}`);
    } else {
      _farthestNodeId = null;
      _set("g-farthest-name", "—");
      _set("g-farthest-text", "");
    }

    _set("g-router-count", data.router_count ?? "—");
    _set("g-client-count", data.client_count ?? "—");

    if (data.hop_dist_raw) {
      _updateHopDistRaw(data.hop_dist_raw, data.hop_used_raw || null);
    }
    if (data.hop_usage_pct_buckets) {
      _updateHopUsage(data.hop_usage_pct_buckets);
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

  function _toHopInt(value) {
    if (value == null) return null;
    const n = parseInt(value, 10);
    return Number.isFinite(n) ? n : null;
  }

  // Dynamic hop list from raw per-value data. Shows mirrored distributions:
  // left side = Hops Used/Hops Away, right side = Hops Left.
  function _updateHopDistRaw(distRaw, usedRaw) {
    const table = document.getElementById("nm-hop-dist-table");
    if (!table) return;

    const normalized = new Map();

    for (const [k, rawCount] of Object.entries(distRaw || {})) {
      const count = Number(rawCount) || 0;
      if (count <= 0) continue;
      if (k === "?") continue;
      const hopLeft = _toHopInt(k);
      if (hopLeft == null) continue;
      normalized.set(hopLeft, (normalized.get(hopLeft) || 0) + count);
    }

    // Backward compatibility: older sessions may have omitted hop_limit (stored as "?")
    // which semantically means 0 hops left.
    const nullCount = Number(distRaw?.["?"]) || 0;
    if (nullCount > 0) {
      normalized.set(0, (normalized.get(0) || 0) + nullCount);
    }

    if (normalized.size === 0) {
      table.innerHTML = "";
      return;
    }

    const maxHopLeft = Math.max(...normalized.keys());

    const hopsLeftCounts = {};
    for (let h = 0; h <= maxHopLeft; h++) {
      hopsLeftCounts[h] = normalized.get(h) || 0;
    }

    const hopsUsedCounts = {};
    if (usedRaw && Object.keys(usedRaw).length > 0) {
      for (const [k, rawCount] of Object.entries(usedRaw)) {
        const hopUsed = _toHopInt(k);
        const count = Number(rawCount) || 0;
        if (hopUsed == null || count <= 0) continue;
        hopsUsedCounts[hopUsed] = (hopsUsedCounts[hopUsed] || 0) + count;
      }
    } else {
      // Fallback for older API responses where only hop-left distribution exists.
      for (const [hopLeft, count] of normalized.entries()) {
        const hopUsed = maxHopLeft - hopLeft;
        hopsUsedCounts[hopUsed] = (hopsUsedCounts[hopUsed] || 0) + count;
      }
    }

    const maxHopUsed = Math.max(0, ...Object.keys(hopsUsedCounts).map(k => _toHopInt(k) ?? 0));
    const maxHopValue = Math.max(maxHopLeft, maxHopUsed);

    const allCounts = [];
    for (let i = 0; i <= maxHopValue; i++) {
      allCounts.push(hopsLeftCounts[i] || 0);
      allCounts.push(hopsUsedCounts[i] || 0);
    }
    const max = Math.max(1, ...allCounts);

    const header = `<tr class="nm-hop-dual-head">
      <th class="nm-hop-used-head" colspan="2">Hops Used</th>
      <th class="nm-hop-mid-head"></th>
      <th class="nm-hop-left-head" colspan="2">Hops Left</th>
    </tr>`;

    const rows = [];
    for (let hop = maxHopValue; hop >= 0; hop--) {
      const usedCount = hopsUsedCounts[hop] || 0;
      const leftCount = hopsLeftCounts[hop] || 0;
      const usedPct = Math.round((usedCount / max) * 100);
      const leftPct = Math.round((leftCount / max) * 100);
      const color = hop >= 4 ? _HOP_COLORS["4+"] : (_HOP_COLORS[String(hop)] || _HOP_COLORS["0"]);

      rows.push(`<tr>
        <td class="nm-hop-count nm-hop-count--left">${usedCount}</td>
        <td class="nm-hop-bar-cell nm-hop-bar-cell--left"><div class="nm-hop-bar nm-hop-bar--left" style="width:${usedPct}%;background:${color}"></div></td>
        <td class="nm-hop-mid-divider">${hop}</td>
        <td class="nm-hop-bar-cell nm-hop-bar-cell--right"><div class="nm-hop-bar" style="width:${leftPct}%;background:${color}"></div></td>
        <td class="nm-hop-count">${leftCount}</td>
      </tr>`);
    }

    table.innerHTML = header + rows.join("");
  }

  function _set(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  function _updateHopUsage(bucketMap) {
    const state = _chartState["nm-hop-usage"];
    if (!state) return;

    const rows = [];
    for (let bucket = 0; bucket <= 100; bucket += 10) {
      const count = Number(bucketMap?.[String(bucket)] ?? 0) || 0;
      rows.push({ bucket, count });
    }
    state.allRows = rows;
    _renderHopUsage();
  }

  function _renderHopUsage() {
    const chart = document.getElementById("nm-hop-usage-chart");
    if (!chart) return;

    const rows = _slicedRows("nm-hop-usage");
    if (!rows.length) {
      chart.innerHTML = `<div class="nm-hop-usage-empty">No data</div>`;
      return;
    }

    const maxCount = Math.max(1, ...rows.map(r => r.count));
    const bars = rows.map(r => {
      const ratio = r.count > 0 ? Math.sqrt(r.count / maxCount) : 0;
      const heightPct = ratio > 0 ? Math.max(2, Math.round(ratio * 88)) : 0;
      const start = r.bucket;
      const end = Math.min(100, r.bucket + 9);
      const title = `${start}%-${end}%: ${r.count} pkts`;
      const barHtml = heightPct > 0
        ? `<div class="nm-hop-usage-bar" style="height:${heightPct}%"></div>`
        : "";
      return `<div class="nm-hop-usage-bucket" title="${title}">
        ${barHtml}
      </div>`;
    }).join("");

    const labelMap = {
      0: "NONE",
      25: "25%",
      50: "50%",
      75: "75%",
      100: "ALL",
    };
    const axisTicks = [];
    for (let i = 0; i <= 100; i += 5) {
      const major = i % 25 === 0;
      const label = labelMap[i] || "";
      axisTicks.push(`<div class="nm-hop-usage-axis-tick${major ? " nm-hop-usage-axis-tick--major" : ""}" style="left:${i}%">
        ${label ? `<span class="nm-hop-usage-axis-label">${label}</span>` : ""}
      </div>`);
    }

    chart.innerHTML = `<div class="nm-hop-usage-plot">
      <div class="nm-hop-usage-centerline"></div>
      <div class="nm-hop-usage-bars">${bars}</div>
      <div class="nm-hop-usage-axis">${axisTicks.join("")}</div>
    </div>`;
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
    "TEXT_MESSAGE_APP":   "#e8a363",
    "POSITION_APP":       "var(--green)",
    "TELEMETRY_APP":      "var(--accent)",
    "NODEINFO_APP":       "var(--purple)",
    "UNKNOWN_PRIVATE_CH": "#6dbad5",
    "UNKNOWN_DIRECT":     "#afefd8",
    "STORE_FORWARD_APP":  "#bddf34",
    "NEIGHBORINFO_APP":   "#73eb3b",
    "RANGE_TEST_APP":     "#c09020",
    "ADMIN_APP":          "#20a3c0",
    "PAXCOUNTER_APP":     "#b389fd",
    "UNKNOWN":            "#f5c2c2",  
  };

  function _pktColor(portnum) {
    return _PKT_COLORS[portnum] || "var(--text-dim)";
  }

  function _pktLabel(portnum) {
    const labels = {
      "TEXT_MESSAGE_APP":   "PUBLIC CH",
      "POSITION_APP":       "POSITION",
      "TELEMETRY_APP":      "TELEMETRY",
      "NODEINFO_APP":       "NODE INFO",
      "UNKNOWN_PRIVATE_CH": "PRIVATE CH",
      "UNKNOWN_DIRECT":     "DIRECT",
      "UNKNOWN":            "ENCRYPTED",
      "NEIGHBORINFO_APP":   "NEIGHBOR",
      "RANGE_TEST_APP":     "RANGE",
      "STORE_FORWARD_APP":  "STORE FORWARD",
      "ADMIN_APP":          "ADMIN",
      "PAXCOUNTER_APP":     "PAX COUNTER",
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
      const pct   = Math.round((r.count / maxCount) * 100);
      const color = _pktColor(r.portnum);
      const label = _pktLabel(r.portnum);
      return `<tr data-traffic-by="portnum" data-traffic-value="${r.portnum}" data-traffic-label="${label}" style="cursor:pointer">
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
    table.innerHTML = rows.map((r, i) => {
      const color = `hsl(${(200 + i * 28) % 360}, 55%, 52%)`;
      const pct   = Math.round((r.count / maxCount) * 100);
      const label = r.hardware_model || "Unknown";
      return `<tr data-traffic-by="hardware" data-traffic-value="${r.hardware_model || ''}" data-traffic-label="${label}" style="cursor:pointer">
        <td class="nm-hop-label" style="min-width:60px;color:${color}">${label}</td>
        <td class="nm-hop-bar-cell"><div class="nm-hop-bar" style="width:${pct}%;background:${color};opacity:0.75"></div></td>
        <td class="nm-hop-count">${r.count}</td>
      </tr>`;
    }).join("");
  }

  // Role breakdown — role label → emoji mapping for display
  const _ROLE_LABELS = {
    "CLIENT":        "📱 CLIENT",
    "CLIENT_MUTE":   "😶 CLIENT_MUTE",
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
    table.innerHTML = rows.map((r, i) => {
      const color = `hsl(${(270 + i * 30) % 360}, 55%, 52%)`;
      const pct   = Math.round((r.count / maxCount) * 100);
      const label = _ROLE_LABELS[r.role] || `👻 ${r.role}`;
      return `<tr data-traffic-by="role" data-traffic-value="${r.role || ''}" data-traffic-label="${label}" style="cursor:pointer">
        <td class="nm-hop-label" style="color:${color}">${label}</td>
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
