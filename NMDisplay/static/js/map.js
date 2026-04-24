/**
 * map.js — Leaflet map, node markers, auto-fit bounds, filtering.
 *
 * Listens for:
 *   "nm:nodes-updated"    — updates/creates node markers
 *   "nm:settings-loaded"  — re-applies label/center mode settings
 *
 * Markers are emoji DivIcons keyed by node role:
 *   CLIENT / CLIENT_*         → 📱
 *   ROUTER / ROUTER_LATE / REPEATER → 📡
 *   TRACKER / LOST_AND_FOUND  → 🐾
 *   SENSOR                    → 📊
 *   unknown / null            → 👻
 *   My node                   → ★
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
        showCoverageOnHover: true,
        maxClusterRadius: 40,
        spiderfyOnMaxZoom: true,
     		showCoverageOnHover: true,
    		zoomToBoundsOnClick: true,
        disableClusteringAtZoom: 10
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

    document.addEventListener("fullscreenchange", () => {
      // Fire twice: once to snap the size immediately, again after the
      // browser finishes its fullscreen transition animation (~300ms).
      setTimeout(() => _map.invalidateSize(), 50);
      setTimeout(() => _map.invalidateSize(), 350);
    });

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
  }

  // ── Node update ───────────────────────────────────────────────

  function _zoomToMyNode() {
    if (_myNodeId == null) return;
    const m = _markers.get(_myNodeId);
    if (!m) return;
    _map.setView(m.getLatLng(), 16);
    _showLabel(_myNodeId);
  }

  function _handleNodes(data) {
    const { nodes, my_node_id } = data;
    _myNodeId    = my_node_id != null ? (my_node_id >>> 0) : null;
    _allGpsNodes = nodes.filter(n => n.lat != null && n.lon != null);

    _updateHopSliderRange(nodes);
    _applyFilters();

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
      return { color: "#e05252", emoji: "📡" };
    if (r === "TRACKER" || r === "TAK" || r === "TAK_TRACKER" || r === "LOST_AND_FOUND")
      return { color: "#3fb950", emoji: "🐾" };
    if (r === "SENSOR")
      return { color: "#d29922", emoji: "📊" };
    if (r === "CLIENT" || r.startsWith("CLIENT_"))
      return { color: "#58a6ff", emoji: "📱" };
    return { color: "#6e7681", emoji: "👻" };
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
            font-size="${isstar ? "13" : "11"}"
            font-family="Apple Color Emoji,Noto Color Emoji,Segoe UI Emoji,Twemoji Mozilla,sans-serif"
            fill="white">${emoji}</text>
    </svg>`;

    return L.divIcon({
      className: "",
      html: svg,
      iconSize:    [25, 41],
      iconAnchor:  [12, 39],
      popupAnchor: [0, -37],
    });
  }

  function _iconForNode(node, isMyNode) {
    if (isMyNode) return _pinIcon("#d4a017", "★");
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

  function flyToNode(nodeId) {
    const m = _markers.get(nodeId);
    if (!m) return;
    // zoomToShowLayer expands any cluster containing the marker,
    // then calls the callback once the marker is visible.
    _cluster.zoomToShowLayer(m, () => {
      _showLabel(nodeId);
    });
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

  function invalidate() {
    if (_map) setTimeout(() => _map.invalidateSize(), 50);
  }

  return { init, invalidate, highlightNode, flyToNode };
})();
