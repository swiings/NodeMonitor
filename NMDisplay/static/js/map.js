/**
 * map.js — Leaflet map, node markers, auto-fit bounds, filtering.
 *
 * Listens for:
 *   "nm:nodes-updated"    — updates/creates node markers
 *   "nm:settings-loaded"  — re-applies label/center mode settings
 *
 * Markers are emoji DivIcons keyed by node role:
 *  CLIENT                          → 📱
 *  CLIENT_BASE                     → 🏡
 *  CLIENT_MUTE                     → 😎
 *  CLIENT_HIDDEN                   → 👻
 *  ROUTER / ROUTER_LATE / REPEATER → 📡
 *  TRACKER / LOST_AND_FOUND        → 🐾
 *  SENSOR                          → 📊
 *  unknown / null                  → 👻
 *  My node                         → ★
 *
 * A colored dot below each emoji shows recency (green/amber/red/grey).
 *
 * Tile layer switcher (Street / Satellite) is built into the map.
 * Source filter removed; only recency filter (ALL / 1H / 6H / 24H) remains.
 */

"use strict";

const NMMap = (() => {
  let _map = null;
  let _cluster = null;          // L.markerClusterGroup
  let _cfg = {};
  let _markers = new Map();     // node_id → L.marker
  let _activeLabel = null;
  let _firstLoad = true;
  let _myNodeId = null;
  let _allGpsNodes = [];        // full node list with GPS, stored for re-filtering

  // Hop filter state
  // _maxHopsFilter: max hops-traversed to show; Infinity = show all (ALL position)
  let _maxHopsFilter = Infinity;
  let _dataMaxHopLimit = 0;     // highest hop_limit seen in current node set
  let _filterIndicator = null;  // Leaflet control showing active hop filter

  // Convex hull state
  let _hullLayer   = null;
  let _hullVisible = false;
  let _hullAreaEl  = null;
  let _hullBtn     = null;

  // Packet-arrival pulse — tracks last_heard per node to detect changes between polls
  let _prevLastHeard = new Map(); // node_id → last_heard timestamp

  // ── Init ──────────────────────────────────────────────────────

  function init(containerId) {
    _map = L.map(containerId, {
      zoomControl: true,
      attributionControl: true,
    }).setView([39.5, -98.35], 4);

    const osmAttr   = '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';
    const hotAttr   = osmAttr + ' | Tiles © <a href="https://www.hotosm.org">HOT</a>';
    const topoAttr  = osmAttr + ' | Tiles © <a href="https://opentopomap.org">OpenTopoMap</a>';

    const standardLayer     = L.tileLayer("/tiles/osm/{z}/{x}/{y}.png",  { maxZoom: 18, attribution: osmAttr,  tileSize: 256, errorTileUrl: "" });
    const humanitarianLayer = L.tileLayer("/tiles/hot/{z}/{x}/{y}.png",  { maxZoom: 18, attribution: hotAttr,  tileSize: 256, errorTileUrl: "" });
    const topoLayer         = L.tileLayer("/tiles/topo/{z}/{x}/{y}.png", { maxZoom: 17, attribution: topoAttr, tileSize: 256, errorTileUrl: "" });

    standardLayer.addTo(_map);

    L.control.layers(
      { "Standard": standardLayer, "Humanitarian": humanitarianLayer, "Topo": topoLayer },
      {},
      { position: "topright" }
    ).addTo(_map);

    L.control.scale({ position: "bottomright", imperial: true, metric: false, maxWidth: 200 }).addTo(_map);

    if (typeof L.markerClusterGroup === "function") {
      _cluster = L.markerClusterGroup({
        zoomToBoundsOnClick: false,
        // disableClusteringAtZoom: 10,
        showCoverageOnHover: false,
        spiderfyOnMaxZoom: true
      });
      _map.addLayer(_cluster);
    } else {
      console.warn("MarkerCluster plugin not loaded — clustering disabled.");
      // Shim: proxy addLayer/removeLayer to the map directly
      _cluster = {
        addLayer:    (m) => m.addTo(_map),
        removeLayer: (m) => m.remove(),
        zoomToShowLayer: (m, cb) => { _map.flyTo(m.getLatLng(), 14); cb && cb(); },
      };
    }

    _map.on("click", () => _dismissLabel());

    // "Go to my node" button
    const MyNodeControl = L.Control.extend({
      options: { position: "topleft" },
      onAdd() {
        const btn = L.DomUtil.create("button", "nm-my-node-btn");
        btn.innerHTML = "&#8982;";
        btn.title = "Zoom to my node";
        L.DomEvent.on(btn, "click", (e) => {
          L.DomEvent.stopPropagation(e);
          _zoomToMyNode();
        });
        return btn;
      },
    });
    new MyNodeControl().addTo(_map);

    // Fullscreen toggle button
    const FullscreenControl = L.Control.extend({
      options: { position: "topleft" },
      onAdd() {
        const btn = L.DomUtil.create("button", "nm-my-node-btn");
        btn.innerHTML = "⛶";
        btn.title = "Toggle fullscreen";
        L.DomEvent.on(btn, "click", (e) => {
          L.DomEvent.stopPropagation(e);
          const el = document.getElementById("nm-map");
          if (!document.fullscreenElement) {
            el.requestFullscreen().catch(() => {});
          } else {
            document.exitFullscreen().catch(() => {});
          }
        });
        return btn;
      },
    });
    new FullscreenControl().addTo(_map);

    // "Fit all markers" button
    const FitAllControl = L.Control.extend({
      options: { position: "topleft" },
      onAdd() {
        const btn = L.DomUtil.create("button", "nm-my-node-btn");
        btn.innerHTML = "&#8865;";
        btn.title = "Fit all markers";
        L.DomEvent.on(btn, "click", (e) => {
          L.DomEvent.stopPropagation(e);
          _fitAllNodes();
        });
        return btn;
      },
    });
    new FitAllControl().addTo(_map);

    document.addEventListener("fullscreenchange", () => {
      // Fire twice: once to snap the size immediately, again after the
      // browser finishes its fullscreen transition animation (~300ms).
      setTimeout(() => _map.invalidateSize(), 50);
      setTimeout(() => _map.invalidateSize(), 350);
    });

    // Convex hull toggle button
    const HullControl = L.Control.extend({
      options: { position: "topleft" },
      onAdd() {
        const btn = L.DomUtil.create("button", "nm-my-node-btn");
        btn.innerHTML = "⬡";
        btn.title = "Toggle convex hull";
        _hullBtn = btn;
        L.DomEvent.on(btn, "click", (e) => {
          L.DomEvent.stopPropagation(e);
          _toggleHull();
        });
        return btn;
      },
    });
    new HullControl().addTo(_map);

    // Hull area display control
    const HullAreaControl = L.Control.extend({
      options: { position: "bottomleft" },
      onAdd() {
        const div = L.DomUtil.create("div", "nm-hull-area hidden");
        _hullAreaEl = div;
        return div;
      },
    });
    new HullAreaControl().addTo(_map);

    // Hop filter active indicator
    const FilterIndicator = L.Control.extend({
      options: { position: "bottomleft" },
      onAdd() {
        const div = L.DomUtil.create("div", "nm-hop-filter-indicator hidden");
        return div;
      },
    });
    _filterIndicator = new FilterIndicator().addTo(_map);

    _initFilterBar();

    // Re-measure after fonts/layout settle, and on any window resize
    setTimeout(() => _map.invalidateSize(), 250);
    window.addEventListener("resize", () => _map.invalidateSize());

    document.addEventListener("nm:nodes-updated",   (e) => _handleNodes(e.detail));
    document.addEventListener("nm:settings-loaded", (e) => { _cfg = e.detail.cfg; });
  }

  // ── Filter bar: hop count slider ──────────────────────────────

  function _initFilterBar() {
    const slider = document.getElementById("nm-hop-slider");
    if (!slider) return;
    slider.addEventListener("input", () => {
      const v = parseInt(slider.value);
      const maxTraversed = _dataMaxHopLimit;  // same as slider.max - 1
      _maxHopsFilter = (v > maxTraversed) ? Infinity : v;
      _updateSliderLabel(v, maxTraversed);
      _applyFilters();
    });
  }

  function _updateSliderLabel(v, maxTraversed) {
    const label = document.getElementById("nm-hop-label");
    if (!label) return;
    if (v > maxTraversed) {
      label.textContent = "ALL";
    } else if (v === 0) {
      label.textContent = "Direct";
    } else {
      label.textContent = `≤ ${v} hop${v === 1 ? "" : "s"}`;
    }
  }

  function _updateHopSliderRange(nodes) {
    // Compute the highest hop_limit in the data to determine "direct"
    // hops_traversed = _dataMaxHopLimit - node.hop_limit
    const hopLimits = nodes
      .map(n => n.hop_limit)
      .filter(v => v != null);
    if (hopLimits.length === 0) return;

    const maxHopLimit = Math.max(...hopLimits);
    if (maxHopLimit === _dataMaxHopLimit) return;  // no change
    _dataMaxHopLimit = maxHopLimit;

    const slider = document.getElementById("nm-hop-slider");
    if (!slider) return;

    const maxTraversed = _dataMaxHopLimit;  // e.g., if maxHopLimit=3, max traversed = 3
    const sliderMax = maxTraversed + 1;     // +1 for the ALL position

    slider.max = sliderMax;

    const dl = document.getElementById("nm-hop-ticks");
    if (dl) {
      const marks = [];
      for (let i = 0; i <= maxTraversed; i++) marks.push(i);
      marks.push(sliderMax);  // ALL position
      dl.innerHTML = marks.map(v => `<option value="${v}"></option>`).join("");
    }

    // If slider is at ALL (max), keep it there
    if (parseInt(slider.value) >= parseInt(slider.max) - 1) {
      slider.value = sliderMax;
      _updateSliderLabel(sliderMax, maxTraversed);
      _maxHopsFilter = Infinity;
    }
  }

  // ── Filter logic ──────────────────────────────────────────────

  function _applyFilters() {
    const visible = _allGpsNodes.filter(n => {
      if (_maxHopsFilter === Infinity) return true;
      const hopLimit = n.hop_limit != null ? n.hop_limit : 0;
      const traversed = _dataMaxHopLimit - hopLimit;
      return traversed <= _maxHopsFilter;
    });
    _syncMarkers(visible);

    // Update filter indicator badge
    if (_filterIndicator) {
      const el = _filterIndicator.getContainer();
      if (!el) return;
      if (_maxHopsFilter === Infinity) {
        el.classList.add("hidden");
      } else {
        el.classList.remove("hidden");
        el.textContent = _maxHopsFilter === 0 ? "⬦ Direct only" : `⬦ ≤ ${_maxHopsFilter} hop${_maxHopsFilter === 1 ? "" : "s"}`;
      }
    }

    _updateHull();
  }

  // ── Node update ───────────────────────────────────────────────

  function _zoomToMyNode() {
    if (_myNodeId == null) return;
    const m = _markers.get(_myNodeId);
    if (!m) return;
    _map.setView(m.getLatLng(), 12);
    _showLabel(_myNodeId);
  }

  function _handleNodes(data) {
    const { nodes, my_node_id } = data;
    _myNodeId    = my_node_id != null ? (my_node_id >>> 0) : null;
    _allGpsNodes = nodes.filter(n => n.lat != null && n.lon != null);

    // Detect nodes whose last_heard advanced since the previous poll.
    // Skip on first load (nothing to compare against yet).
    const changedIds = new Set();
    let changedNonRef = 0;
    const isFirstLoad = _prevLastHeard.size === 0;
    for (const n of nodes) {
      const prev = _prevLastHeard.get(n.node_id);
      if (!isFirstLoad && prev != null && n.last_heard != null && n.last_heard > prev) {
        changedIds.add(n.node_id);
        if (!n.is_my_node) changedNonRef++;
      }
      if (n.last_heard != null) _prevLastHeard.set(n.node_id, n.last_heard);
    }

    _updateHopSliderRange(nodes);
    _applyFilters();  // creates/updates all markers

    // Pulse all changed nodes (including reference node — telemetry should be visible)
    for (const id of changedIds) _triggerPulse(id);
    // Beep only for non-reference-node packets
    if (changedNonRef > 0) {
      document.dispatchEvent(new CustomEvent("nm:packets-received", { detail: { count: changedNonRef } }));
    }

    if (_firstLoad && _allGpsNodes.length > 0) {
      _autoFitBounds(_allGpsNodes);
      _firstLoad = false;
    }
  }

  function _syncMarkers(visibleNodes) {
    const seen = new Set(visibleNodes.map(n => n.node_id));

    for (const node of visibleNodes) {
      if (_markers.has(node.node_id)) {
        _updateMarker(node);
      } else {
        _createMarker(node);
      }
    }

    // Remove markers for nodes now filtered out
    for (const [id, m] of _markers) {
      if (!seen.has(id)) {
        _cluster.removeLayer(m);
        _markers.delete(id);
        if (_activeLabel === id) _activeLabel = null;
      }
    }
  }

  function _createMarker(node) {
    const isMyNode = node.is_my_node === true;
    const m = L.marker([node.lat, node.lon], { icon: _iconForNode(node, isMyNode) });

    m.bindTooltip(_labelText(node), {
      permanent: false,
      direction: "top",
      className: "nm-node-label",
      offset: [0, -38],
    });

    m.bindPopup(_popupHtml(node));

    m.on("click", (e) => {
      L.DomEvent.stopPropagation(e);
      _showLabel(node.node_id);
    });

    _cluster.addLayer(m);
    _markers.set(node.node_id, m);
    m._nmNode = node;
  }

  function _updateMarker(node) {
    const m = _markers.get(node.node_id);
    const isMyNode = node.is_my_node === true;
    m.setLatLng([node.lat, node.lon]);
    m.setIcon(_iconForNode(node, isMyNode));
    m.setTooltipContent(_labelText(node));
    m.setPopupContent(_popupHtml(node));
    m._nmNode = node;
  }

  // ── Drop-pin markers ──────────────────────────────────────────

  // Meshtastic role enum — protobuf stores as integer; map to canonical names.
  const _ROLE_INT_MAP = {
    "0": "CLIENT", "1": "CLIENT_MUTE",  "2": "ROUTER",
    "3": "ROUTER_CLIENT", "4": "CLIENT_HIDDEN", "5": "LOST_AND_FOUND",
    "6": "SENSOR", "7": "TAK", "8": "TAK_TRACKER", "9": "REPEATER", "10": "TRACKER",
  };

  // Role → { color, emoji } for marker display
  function _roleStyle(role) {
    const r = (_ROLE_INT_MAP[String(role ?? "0")] || String(role ?? "CLIENT")).toUpperCase();
    if (r === "ROUTER" || r === "ROUTER_CLIENT" || r === "ROUTER_LATE" || r === "REPEATER")
      return { color: "#e0525277", emoji: "📡" };
    if (r === "CLIENT_BASE")
      return { color: "#46e0a291", emoji: "🏡" };
    if (r === "TRACKER" || r === "TAK" || r === "TAK_TRACKER" || r === "LOST_AND_FOUND")
      return { color: "#a061a79f", emoji: "🐾" };
    if (r === "SENSOR")
      return { color: "#d29a22a2", emoji: "📊" };
    if (r === "CLIENT")
      return { color: "#6faef7b3", emoji: "📱" };
    if (r === "CLIENT_MUTE")
      return { color: "#21314293", emoji: "😎" };
    if (r === "CLIENT_HIDDEN")
      return { color: "#21314280", emoji: "👻" };
    return { color: "#6e7681a5", emoji: "👻" };
  }
  /**
   * Leaflet-style teardrop marker as a pure SVG DivIcon.
   * Emoji (or ★ for my node) rendered via SVG <text> — no shadow.
   */
  function _pinIcon(color, emoji) {
    const isstar = emoji === "★";
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="25" height="41" viewBox="0 0 25 41">
      <path d="M12.5,39 C12.5,39 2,25.5 2,13.5 A10.5,10.5 0 1 1 23,13.5 C23,25.5 12.5,39 Z"
            fill="${color}" stroke="rgba(0,0,0,0.45)" stroke-width="1.5"/>
      <text x="12.5" y="14" text-anchor="middle" dominant-baseline="middle"
            font-size="${isstar ? "13" : "14"}"
            font-family="Apple Color Emoji,Noto Color Emoji,Segoe UI Emoji,Twemoji Mozilla,sans-serif"
            fill="white">${emoji}</text>
    </svg>`;

    // Wrap in a child div so the pulse animation can use transform without
    // conflicting with Leaflet's own transform: translate() on the marker element.
    return L.divIcon({
      className: "",
      html: `<div class="nm-pin-wrap">${svg}</div>`,
      iconSize:    [25, 41],
      iconAnchor:  [12, 39],
      popupAnchor: [0, -37],
    });
  }

  function _iconForNode(node, isMyNode) {
    if (isMyNode) return _pinIcon("#2f08f2", "★");
    const { color, emoji } = _roleStyle(node.role);
    return _pinIcon(color, emoji);
  }

  // ── Label management ──────────────────────────────────────────

  function _showLabel(nodeId) {
    if (_activeLabel !== null && _activeLabel !== nodeId) {
      const prev = _markers.get(_activeLabel);
      if (prev) prev.closeTooltip();
    }
    const m = _markers.get(nodeId);
    if (!m) return;
    _activeLabel = nodeId;
    m.openTooltip();
    if (!_map.getBounds().contains(m.getLatLng())) {
      _map.panTo(m.getLatLng());
    }
  }

  function _dismissLabel() {
    if (_activeLabel !== null) {
      const m = _markers.get(_activeLabel);
      if (m) m.closeTooltip();
      _activeLabel = null;
    }
  }

  function highlightNode(nodeId) {
    _showLabel(nodeId);
  }

  function _triggerPulse(nodeId) {
    const m = _markers.get(nodeId);
    if (!m) return;
    const el = m.getElement();
    if (!el) return;
    const wrap = el.querySelector(".nm-pin-wrap");
    if (!wrap) return;
    // Remove first to restart the animation if it's already playing
    wrap.classList.remove("nm-marker-pulse");
    void wrap.offsetWidth; // force reflow
    wrap.classList.add("nm-marker-pulse");
    setTimeout(() =>  wrap.classList.remove("nm-marker-pulse"),  10000);
  }

  function flyToNode(nodeId) {
    const m = _markers.get(nodeId);
    if (!m) return;
    _map.setView(m.getLatLng(), 12);
    _showLabel(nodeId);
  }

  // ── Colors ────────────────────────────────────────────────────


  function _labelText(node) {
    const name = node.short_name || node.node_id_hex || "?";
    const rssi = node.rssi != null ? ` ${node.rssi}` : "";
    return `${name}${rssi}`;
  }

  // ── Popup ─────────────────────────────────────────────────────

  function _popupHtml(node) {
    const ts = node.last_heard
      ? new Date(node.last_heard * 1000).toLocaleString()
      : "—";

    const hopsAway = (node.hop_limit != null && _dataMaxHopLimit > 0)
      ? (_dataMaxHopLimit - node.hop_limit)
      : null;
    const distMi = node.distance_km != null
      ? `${(node.distance_km * 0.621371).toFixed(1)} mi`
      : null;

    const rows = [
      ["ID",         node.node_id_hex || "—"],
      ["Short",      node.short_name  || "—"],
      ["Long",       node.long_name   || "—"],
      ["HW",         node.hardware_model || "—"],
      ["Role",       node.role || "—"],
      ["Hops away",  hopsAway != null ? hopsAway : "—"],
      ["Distance",   distMi || "—"],
      ["RSSI",       node.rssi != null ? `${node.rssi} dBm` : "—"],
      ["SNR",        node.snr  != null ? `${node.snr} dB`  : "—"],
      ["Battery",    node.battery_level != null ? `${node.battery_level}%` : "—"],
      ["Position",   `${node.lat.toFixed(5)}, ${node.lon.toFixed(5)}`],
      ["Packets",    node.total_packets],
      ["Last heard", ts],
    ];
    const trs = rows.map(([k, v]) =>
      `<tr><td style="color:#888;padding-right:4px">${k}</td><td>${v}</td></tr>`
    ).join("");
    return `<table style="font-size:10px;font-family:monospace;line-height:1.4">${trs}</table>`;
  }

  // ── Bounds ────────────────────────────────────────────────────

  function _autoFitBounds(gpsNodes) {
    if (!_map || gpsNodes.length === 0) return;
    const bounds = L.latLngBounds(gpsNodes.map(n => [n.lat, n.lon]));
    _map.fitBounds(bounds, { padding: [40, 40], maxZoom: 14 });
  }

  function _fitAllNodes() {
    const visible = _allGpsNodes.filter(n => {
      if (_maxHopsFilter === Infinity) return true;
      const traversed = _dataMaxHopLimit - (n.hop_limit ?? 0);
      return traversed <= _maxHopsFilter;
    });
    if (visible.length === 0) return;
    const bounds = L.latLngBounds(visible.map(n => [n.lat, n.lon]));
    _map.fitBounds(bounds, { padding: [40, 40], maxZoom: 14 });
  }

  function invalidate() {
    if (_map) setTimeout(() => _map.invalidateSize(), 50);
  }

  // ── Convex Hull ───────────────────────────────────────────────

  function _toggleHull() {
    _hullVisible = !_hullVisible;
    if (_hullBtn) _hullBtn.classList.toggle("active", _hullVisible);
    if (_hullVisible) {
      _updateHull();
    } else {
      if (_hullLayer) { _hullLayer.remove(); _hullLayer = null; }
      if (_hullAreaEl) _hullAreaEl.classList.add("hidden");
    }
  }

  function _updateHull() {
    if (!_hullVisible) return;
    if (_hullLayer) { _hullLayer.remove(); _hullLayer = null; }

    const pts = _allGpsNodes.filter(n => {
      if (_maxHopsFilter === Infinity) return true;
      const traversed = _dataMaxHopLimit - (n.hop_limit ?? 0);
      return traversed <= _maxHopsFilter;
    });

    if (pts.length < 3) {
      if (_hullAreaEl) {
        _hullAreaEl.classList.remove("hidden");
        _hullAreaEl.textContent = "⬡ Need ≥ 3 GPS nodes";
      }
      return;
    }

    const hullPts = _convexHull(pts.map(n => ({ lat: n.lat, lng: n.lon })));
    const areaSqMi = _polygonAreaSqMi(hullPts);

    _hullLayer = L.polygon(
      hullPts.map(p => [p.lat, p.lng]),
      { color: "#58a6ff", fillColor: "#58a6ff", fillOpacity: 0.06,
        weight: 1.5, dashArray: "6,4", interactive: false }
    ).addTo(_map);

    if (_hullAreaEl) {
      _hullAreaEl.classList.remove("hidden");
      let areaStr;
      if (areaSqMi < 0.01)     areaStr = `${(areaSqMi * 640).toFixed(0)} acres`;
      else if (areaSqMi < 100) areaStr = `${areaSqMi.toFixed(1)} sq mi`;
      else                      areaStr = `${Math.round(areaSqMi).toLocaleString()} sq mi`;
      _hullAreaEl.textContent = `⬡ ${areaStr}`;
    }
  }

  // Gift-wrapping (Jarvis march) convex hull on {lat, lng} points
  function _convexHull(points) {
    const n = points.length;
    if (n < 3) return points.slice();

    let start = 0;
    for (let i = 1; i < n; i++) {
      if (points[i].lng < points[start].lng ||
         (points[i].lng === points[start].lng && points[i].lat < points[start].lat)) {
        start = i;
      }
    }

    const hull = [];
    let cur = start;
    do {
      hull.push(points[cur]);
      let nxt = (cur + 1) % n;
      for (let i = 0; i < n; i++) {
        // cross product: negative means points[i] is more counter-clockwise
        const cross = (points[nxt].lng - points[cur].lng) * (points[i].lat - points[cur].lat)
                    - (points[nxt].lat - points[cur].lat) * (points[i].lng - points[cur].lng);
        if (cross < 0) nxt = i;
      }
      cur = nxt;
    } while (cur !== start && hull.length <= n);

    return hull;
  }

  // Equirectangular shoelace area in square miles
  function _polygonAreaSqMi(pts) {
    const n = pts.length;
    if (n < 3) return 0;
    const R = 3958.8;
    const toRad = d => d * Math.PI / 180;
    const latMid = pts.reduce((s, p) => s + p.lat, 0) / n;
    const cosLat = Math.cos(toRad(latMid));
    let area = 0;
    for (let i = 0; i < n; i++) {
      const j = (i + 1) % n;
      const xi = toRad(pts[i].lng) * cosLat;
      const yi = toRad(pts[i].lat);
      const xj = toRad(pts[j].lng) * cosLat;
      const yj = toRad(pts[j].lat);
      area += xi * yj - xj * yi;
    }
    return Math.abs(area / 2) * R * R;
  }

  return { init, invalidate, highlightNode, flyToNode };
})();
