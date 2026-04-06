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

  function init() {
    document.addEventListener("nm:gauges-updated", (e) => _update(e.detail));

    document.getElementById("gauge-closest").addEventListener("click", () => {
      if (_closestNodeId != null) NMMap.flyToNode(_closestNodeId);
    });
    document.getElementById("gauge-farthest").addEventListener("click", () => {
      if (_farthestNodeId != null) NMMap.flyToNode(_farthestNodeId);
    });
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
      _set("g-closest-name", data.closest.short_name);
      _set("g-closest-dist", `${data.closest.distance_mi} mi`);
    } else {
      _closestNodeId = null;
      _set("g-closest-name", "—");
      _set("g-closest-dist", "");
    }

    if (data.farthest) {
      _farthestNodeId = data.farthest.node_id;
      _set("g-farthest-name", data.farthest.short_name);
      _set("g-farthest-dist", `${data.farthest.distance_mi} mi`);
    } else {
      _farthestNodeId = null;
      _set("g-farthest-name", "—");
      _set("g-farthest-dist", "");
    }

    _set("g-router-count", data.router_count ?? "—");
    _set("g-client-count", data.client_count ?? "—");

    if (data.hop_dist) {
      _updateHopDist(data.hop_dist);
      _drawHopPie(data.hop_dist);
    }
  }

  // Colors per bucket: 4+=accent blue, 3=green, 2=amber, 1=red-orange, 0=grey
  const _HOP_COLORS = {
    "4+": "#58a6ff",
    "3":  "#3fb950",
    "2":  "#d29922",
    "1":  "#f0883e",
    "0":  "#484f58",
  };

  function _drawHopPie(dist) {
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
  }

  function _updateHopDist(dist) {
    const buckets = ["4+", "3", "2", "1", "0"];
    const max = Math.max(1, ...buckets.map(k => dist[k] || 0));
    for (const k of buckets) {
      const count = dist[k] || 0;
      const id = k === "4+" ? "4plus" : k;
      const countEl = document.getElementById(`hc-${id}`);
      const barEl   = document.getElementById(`hb-${id}`);
      if (countEl) countEl.textContent = count;
      if (barEl)   barEl.style.width = `${Math.round((count / max) * 100)}%`;
    }
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
