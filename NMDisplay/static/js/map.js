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
 *  CLIENT_MUTE                     → 😶
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
  let _myNodeBtn = null;        // reference to "go to my node" button element

  // Hop filter state
  // _maxHopsFilter: max hops-away to show; Infinity = show all (ALL position)
  let _maxHopsFilter = Infinity;
  let _dataMaxHopLimit = 0;     // kept name for compatibility; stores highest hops_away seen
  let _filterIndicator = null;  // Leaflet control showing active hop filter

  // Age filter state — max age in minutes; Infinity = show all
  const _AGE_STEPS  = [1, 5, 15, 30, 60, 360, 1440, Infinity];
  const _AGE_LABELS = ["1m", "5m", "15m", "30m", "1h", "6h", "1d", "ALL"];
  // const _AGE_STEPS  = [15, 60, 720, 1440, 2880, 4320, 5760, Infinity];
  // const _AGE_LABELS = ["15m", "1h", "12h", "1d", "2d", "3d", "4d", "ALL"];

  let _maxAgeFilter = Infinity;

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

    // ── Attribution strings ────────────────────────────────────────────────────
    const osmAttr    = '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';
    const hotAttr    = osmAttr + ' | Tiles © <a href="https://www.hotosm.org">HOT</a>';
    const topoAttr   = osmAttr + ' | Tiles © <a href="https://opentopomap.org">OpenTopoMap</a>';
    const stadiaAttr = '© <a href="https://www.stadiamaps.com/" target="_blank">Stadia Maps</a> © <a href="https://openmaptiles.org/" target="_blank">OpenMapTiles</a> ' + osmAttr;
    const stadiaStamenAttr = '© <a href="https://www.stadiamaps.com/" target="_blank">Stadia Maps</a> © <a href="https://www.stamen.com/" target="_blank">Stamen Design</a> © <a href="https://openmaptiles.org/" target="_blank">OpenMapTiles</a> ' + osmAttr;
    const tfAttr     = '© <a href="http://www.thunderforest.com/">Thunderforest</a>, ' + osmAttr;

    // ── Original providers ─────────────────────────────────────────────────────
    const standardLayer     = L.tileLayer("/tiles/osm/{z}/{x}/{y}.png",  { maxZoom: 18, attribution: osmAttr,    tileSize: 256, errorTileUrl: "" });
    const humanitarianLayer = L.tileLayer("/tiles/hot/{z}/{x}/{y}.png",  { maxZoom: 18, attribution: hotAttr,    tileSize: 256, errorTileUrl: "" });
    const topoLayer         = L.tileLayer("/tiles/topo/{z}/{x}/{y}.png", { maxZoom: 17, attribution: topoAttr,   tileSize: 256, errorTileUrl: "" });

    // ── Stadia Maps layers (no API key required for testing) ───────────────────
    // REMOVE the next line to drop "Stadia Dark".
    const stadiaDark       = L.tileLayer("/tiles/stadia_smooth_dark/{z}/{x}/{y}.png", { maxZoom: 20, attribution: stadiaAttr,      tileSize: 256, errorTileUrl: "" });
    // ── End Stadia block ───────────────────────────────────────────────────────

    // ── Thunderforest layers (API key required — set in app.py) ───────────────
    // REMOVE the next line to drop "TF atlas".
    const tfAtlas     = L.tileLayer("/tiles/tf_atlas/{z}/{x}/{y}.png",     { maxZoom: 22, attribution: tfAttr, tileSize: 256, errorTileUrl: "" });
    // ── End Thunderforest block ────────────────────────────────────────────────

    const _addTileRetry = (layer) => {
      layer.on("tileerror", (err) => {
        const tile = err.tile;
        const retries = parseInt(tile.dataset.nmRetries || "0");
        if (retries < 3) {
          tile.dataset.nmRetries = retries + 1;
          setTimeout(() => {
            const base = tile.src.replace(/[?&]nm_retry=\d+/, "");
            tile.src = base + (base.includes("?") ? "&" : "?") + "nm_retry=" + tile.dataset.nmRetries;
          }, 1000 * (retries + 1));
        }
      });
    };
    [standardLayer, humanitarianLayer, topoLayer, stadiaDark, tfAtlas].forEach(_addTileRetry);

    standardLayer.addTo(_map);

    // ── Layer control ──────────────────────────────────────────────────────────
    // To remove a provider: delete its entry here AND its layer declaration above
    L.control.layers(
      {
        // Original
        "Standard":          standardLayer,
        "Humanitarian":      humanitarianLayer,
        "Topo":              topoLayer,
        // Stadia — REMOVE entries to drop individual layers
        "Stadia Dark":       stadiaDark,
        // Thunderforest — REMOVE entries to drop individual layers (API key required)
        "TF Atlas":          tfAtlas,
      },
      {},
      { position: "topright" }
    ).addTo(_map);
    // ── End layer control block ────────────────────────────────────────────────

    L.control.scale({ position: "bottomright", imperial: true, metric: false, maxWidth: 200 }).addTo(_map);

    if (typeof L.markerClusterGroup === "function") {
      _cluster = L.markerClusterGroup({
        zoomToBoundsOnClick: true,
        // disableClusteringAtZoom: 10,
        showCoverageOnHover: true,
        spiderfyOnMaxZoom: true,
        maxClusterRadius: 25,
      });
      _map.addLayer(_cluster);
    } else {
      console.warn("MarkerCluster plugin not loaded — clustering disabled.");
      // Shim: proxy addLayer/removeLayer to the map directly
      _cluster = {
        addLayer:    (m) => m.addTo(_map),
        removeLayer: (m) => m.remove(),
        zoomToShowLayer: (m, cb) => { _map.flyTo(m.getLatLng(), 15); cb && cb(); },
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
        _myNodeBtn = btn;
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

    // Move floating overlays into/out of the fullscreen element so they
    // stay above the map in the browser's fullscreen stacking context.
    const _overlayIds = ["nm-traffic-window"];
    document.addEventListener("fullscreenchange", () => {
      const mapEl = document.getElementById("nm-map");
      const entering = !!document.fullscreenElement;
      _overlayIds.forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        if (entering) {
          mapEl.appendChild(el);
        } else {
          document.body.appendChild(el);
        }
      });
    });

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

    // Zoom level toast (bottom-right, near scale bar)
    const ZoomToastControl = L.Control.extend({
      options: { position: "bottomright" },
      onAdd() {
        const div = L.DomUtil.create("div", "nm-zoom-toast hidden");
        return div;
      },
    });
    const _zoomToast = new ZoomToastControl().addTo(_map);
    let _zoomToastTimer = null;
    _map.on("zoomend", () => {
      const el = _zoomToast.getContainer();
      if (!el) return;
      el.textContent = `Zoom: ${_map.getZoom()}`;
      el.classList.remove("hidden");
      clearTimeout(_zoomToastTimer);
      _zoomToastTimer = setTimeout(() => el.classList.add("hidden"), 2000);
    });

    _initFilterBar();
    _initAgeFilterBar();
    _initClusterSlider();

    // Re-measure after fonts/layout settle, and on any window resize
    setTimeout(() => _map.invalidateSize(), 250);
    window.addEventListener("resize", () => _map.invalidateSize());

    document.addEventListener("nm:nodes-updated",   (e) => _handleNodes(e.detail));
    document.addEventListener("nm:settings-loaded", (e) => { _cfg = e.detail.cfg; });

    _map.on("popupopen", (e) => {
      const popupEl = e.popup.getElement();
      if (!popupEl) return;

      const trafficBtn = popupEl.querySelector(".nm-traffic-btn");
      if (trafficBtn) {
        trafficBtn.addEventListener("click", (ev) => {
          L.DomEvent.stopPropagation(ev);
          const nodeId = parseInt(trafficBtn.dataset.nodeId, 10);
          const marker = e.popup._source;
          const node   = marker?._nmNode;
          const label  = node?.short_name || `!${(nodeId >>> 0).toString(16).padStart(8, "0")}`;
          document.dispatchEvent(new CustomEvent("nm:open-traffic", {
            detail: { by: "node", value: nodeId, label }
          }));
          e.popup.remove();
        });
      }

      const deleteBtn = popupEl.querySelector(".nm-node-delete-btn");
      if (!deleteBtn) return;
      deleteBtn.addEventListener("click", async (ev) => {
        L.DomEvent.stopPropagation(ev);
        const nodeId = parseInt(deleteBtn.dataset.nodeId, 10);
        const marker = e.popup._source;
        const node   = marker?._nmNode;
        const label  = node?.short_name || node?.node_id_hex || nodeId;
        if (!confirm(`Delete node ${label} from this session?`)) return;
        const resp = await fetch(`/api/nodes/${nodeId}`, { method: "DELETE" });
        if (resp.ok) {
          e.popup.remove();
          document.dispatchEvent(new CustomEvent("nm:request-poll"));
        } else {
          const data = await resp.json().catch(() => ({}));
          alert(data.error || "Delete failed");
        }
      });
    });
  }

  // ── Filter bar: hop count slider ──────────────────────────────

  function _initFilterBar() {
    const slider = document.getElementById("nm-hop-slider");
    if (!slider) return;
    slider.addEventListener("input", () => {
      const v = parseInt(slider.value);
      const maxHopsAway = _dataMaxHopLimit;  // same as slider.max - 1
      _maxHopsFilter = (v > maxHopsAway) ? Infinity : v;
      _updateSliderLabel(v, maxHopsAway);
      _applyFilters();
    });
  }

  function _updateSliderLabel(v, maxHopsAway) {
    const label = document.getElementById("nm-hop-label");
    if (!label) return;
    if (v > maxHopsAway) {
      label.textContent = "ALL";
    } else if (v === 0) {
      label.textContent = "Direct";
    } else {
      label.textContent = `≤ ${v} hop${v === 1 ? "" : "s"}`;
    }
  }

  function _updateHopSliderRange(nodes) {
    const hopsAwayVals = nodes
      .map(n => n.hops_away)
      .filter(v => v != null && Number.isFinite(v));
    if (hopsAwayVals.length === 0) return;

    const maxHopsAway = Math.max(...hopsAwayVals);
    if (maxHopsAway === _dataMaxHopLimit) return;  // no change
    _dataMaxHopLimit = maxHopsAway;

    const slider = document.getElementById("nm-hop-slider");
    if (!slider) return;

    const maxHopsAwayNow = _dataMaxHopLimit;
    const sliderMax = maxHopsAwayNow + 1;   // +1 for the ALL position

    slider.max = sliderMax;

    const dl = document.getElementById("nm-hop-ticks");
    if (dl) {
      const marks = [];
      for (let i = 0; i <= maxHopsAwayNow; i++) marks.push(i);
      marks.push(sliderMax);  // ALL position
      dl.innerHTML = marks.map(v => `<option value="${v}"></option>`).join("");
    }

    // If slider is at ALL (max), keep it there
    if (parseInt(slider.value) >= parseInt(slider.max) - 1) {
      slider.value = sliderMax;
      _updateSliderLabel(sliderMax, maxHopsAwayNow);
      _maxHopsFilter = Infinity;
    }
  }

  // ── Age filter bar ────────────────────────────────────────────

  function _initAgeFilterBar() {
    const slider = document.getElementById("nm-age-slider");
    if (!slider) return;
    slider.max = _AGE_STEPS.length - 1;
    slider.value = _AGE_STEPS.length - 1;  // ALL by default
    slider.addEventListener("input", () => {
      const v = parseInt(slider.value);
      _maxAgeFilter = _AGE_STEPS[v];
      const label = document.getElementById("nm-age-label");
      if (label) label.textContent = _AGE_LABELS[v];
      _applyFilters();
    });
  }

  // ── Cluster zoom slider ───────────────────────────────────────

  function _initClusterSlider() {
    const slider = document.getElementById("nm-cluster-slider");
    if (!slider || typeof L.markerClusterGroup !== "function") return;

    const maxZ = isFinite(_map.getMaxZoom()) ? _map.getMaxZoom() : 18;
    slider.max = maxZ;

    const saved = parseInt(localStorage.getItem("nm-cluster-zoom") ?? "0");
    const initial = Math.min(Math.max(saved, 0), maxZ);
    slider.value = initial;
    const label = document.getElementById("nm-cluster-label");
    if (label) label.textContent = initial === 0 ? "OFF" : `Z${initial}`;
    if (initial > 0) _applyClusterZoom(initial);

    slider.addEventListener("input", () => {
      const v = parseInt(slider.value);
      if (label) label.textContent = v === 0 ? "OFF" : `Z${v}`;
      localStorage.setItem("nm-cluster-zoom", v);
      _applyClusterZoom(v);
    });
  }

  function _applyClusterZoom(zoomLevel) {
    if (!_cluster || typeof _cluster.clearLayers !== "function") return;
    _cluster.options.disableClusteringAtZoom = zoomLevel === 0 ? undefined : zoomLevel;
    // Force the cluster to recompute by clearing and re-adding all markers
    const allMarkers = Array.from(_markers.values());
    _cluster.clearLayers();
    if (typeof _cluster.addLayers === "function") {
      _cluster.addLayers(allMarkers);
    } else {
      for (const m of allMarkers) _cluster.addLayer(m);
    }
  }

  // ── Filter logic ──────────────────────────────────────────────

  function _applyFilters() {
    const nowSec = Math.floor(Date.now() / 1000);
    const visible = _allGpsNodes.filter(n => {
      if (_maxHopsFilter !== Infinity) {
        const hopsAway = (n.hops_away != null && Number.isFinite(n.hops_away)) ? n.hops_away : null;
        if (hopsAway == null || hopsAway > _maxHopsFilter) return false;
      }
      if (_maxAgeFilter !== Infinity) {
        if (n.last_heard == null) return false;
        const ageMinutes = (nowSec - n.last_heard) / 60;
        if (ageMinutes > _maxAgeFilter) return false;
      }
      return true;
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
    if (_myNodeId == null || !_markers.has(_myNodeId)) {
      if (_myNodeBtn) _showFloatTooltip(_myNodeBtn, "Location Unknown");
      return;
    }
    const m = _markers.get(_myNodeId);
    _map.setView(m.getLatLng(), 14);
    _showLabel(_myNodeId);
  }

  let _floatTooltipTimer = null;
  function _showFloatTooltip(anchorEl, msg) {
    const tooltip = document.getElementById("nm-float-tooltip");
    if (!tooltip) return;
    tooltip.textContent = msg;
    const rect = anchorEl.getBoundingClientRect();
    tooltip.style.left = `${rect.right + 8}px`;
    tooltip.style.top  = `${Math.round(rect.top + rect.height / 2) - 11}px`;
    tooltip.classList.remove("hidden");
    clearTimeout(_floatTooltipTimer);
    _floatTooltipTimer = setTimeout(() => tooltip.classList.add("hidden"), 1800);
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
      offset: [0, -20],
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
    const isMyNode = false; // node.is_my_node === true;
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
    if (r === "VEHICLE")
      return { color: "#8ec0ffa5", emoji: "🚙" };
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
      return { color: "#21314293", emoji: "😶" };
    if (r === "CLIENT_HIDDEN")
      return { color: "#21314280", emoji: "👻" };
    return { color: "#6e7681a5", emoji: "👻" };
  }
  function _emojiIcon(emoji) {
    return L.divIcon({
      className: "",
      html: `<div class="nm-pin-wrap">${emoji}</div>`,
      iconSize:    [30, 30],
      iconAnchor:  [15, 15],
      popupAnchor: [0, -20],
    });
  }

  function _iconForNode(node, isMyNode) {
    if (isMyNode) return _emojiIcon("★");
    const { emoji } = _roleStyle(node.role);
    return _emojiIcon(emoji);
  }

  // ── Label management ──────────────────────────────────────────✪

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

    // Icon pulse: 0.5 s expand 20%, 1.5 s return (2 s total)
    const el = m.getElement();
    if (el) {
      const wrap = el.querySelector(".nm-pin-wrap");
      if (wrap) {
        wrap.classList.remove("nm-marker-pulse");
        void wrap.offsetWidth;  // force reflow to restart animation
        wrap.classList.add("nm-marker-pulse");
        setTimeout(() => wrap.classList.remove("nm-marker-pulse"), 3500);
      }
    }

    // Expanding circle ripple via Leaflet circleMarker (pixel radius, zoom-stable)
    const ripple = L.circleMarker(m.getLatLng(), {
      radius: 6,
      color: "#7ec8ff",
      fillColor: "transparent",
      weight: 2.5,
      opacity: 1.0,
      interactive: false,
    }).addTo(_map);

    const startMs = Date.now();
    const duration = 2500;
    const markerPx = _map.latLngToContainerPoint(m.getLatLng());
    const mapSize  = _map.getSize();
    const maxRadius = Math.ceil(Math.max(
      Math.hypot(markerPx.x,              markerPx.y),
      Math.hypot(mapSize.x - markerPx.x,  markerPx.y),
      Math.hypot(markerPx.x,              mapSize.y - markerPx.y),
      Math.hypot(mapSize.x - markerPx.x,  mapSize.y - markerPx.y)
    ));

    const _animateRipple = () => {
      const progress = Math.min(1, (Date.now() - startMs) / duration);
      ripple.setRadius(6 + (maxRadius - 6) * progress);
      ripple.setStyle({ opacity: 1.0 * (1 - progress) });
      if (progress < 1) {
        requestAnimationFrame(_animateRipple);
      } else {
        ripple.remove();
      }
    };
    requestAnimationFrame(_animateRipple);
  }

  function flyToNode(nodeId) {
    const m = _markers.get(nodeId);
    if (!m) return;
    _map.setView(m.getLatLng(), 15);
    // Programmatic navigation should only reveal the tooltip.
    // Popup remains user-driven via marker click.
    _cluster.zoomToShowLayer(m, () => _showLabel(nodeId));
  }

  function hasMarker(nodeId) {
    return _markers.has(nodeId);
  }

  // ── Colors ────────────────────────────────────────────────────


  function _labelText(node) {
    const name = node.short_name || node.node_id_hex; // || String(node.node_id);
    return `${name}`;
  }

  function _locationSourceLabel(source) {
    switch (source) {
      case "LOC_UNSET":
        return "Unknown";
      case "LOC_INTERNAL":
        return "Hardware GPS";
      case "LOC_EXTERNAL":
        return "Phone/Host GPS";
      case "LOC_MANUAL":
        return "Manual Position";
      default:
        return "Unknown";
    }
  }

  // ── Popup ─────────────────────────────────────────────────────

  function _popupHtml(node) {
    const ts = node.last_heard
      ? new Date(node.last_heard * 1000).toLocaleString()
      : "—";
    const tsFirst = node.first_seen
      ? new Date(node.first_seen * 1000).toLocaleString()
      : "—";

    const hopsAway = (node.hops_away != null && Number.isFinite(node.hops_away))
      ? node.hops_away
      : null;
    const distMi = node.distance_km != null
      ? `${(node.distance_km * 0.621371).toFixed(1)} mi`
      : null;
    const altStr = node.altitude_m != null
      ? `${(node.altitude_m * 3.281).toFixed(0)} ft`
      : "—";

    const positionStr = (node.lat != null && node.lon != null)
      ? `${node.lat.toFixed(5)}, ${node.lon.toFixed(5)}`
      : "—";

    const rows = [
      ["ID",         node.node_id_hex || "—"],
      ["Short",      node.short_name  || "—"],
      ["Long",       node.long_name   || "—"],
      ["HW",         node.hardware_model || "—"],
      ["Role",       node.role || "—"],
      ["Packet Source", node.packet_source || "—"],
      ["Hops away",  hopsAway != null ? hopsAway : "—"],
      ["Distance",   distMi || "—"],
      ["RSSI (dBm)", node.rssi != null ? `${node.rssi}` : "—"],
      ["SNR (dB)",   node.snr  != null ? `${node.snr}`  : "—"],
      ["Battery (%)", node.battery_level != null ? `${node.battery_level}` : "—"],
      ["Position",   positionStr],
      ["Location Source", _locationSourceLabel(node.source)],
      ["Sats",       node.sats != null ? String(node.sats) : "—"],
      ...(node.precision_bits != null ? [["Precision", _precisionLabel(node.precision_bits)]] : []),
      ["Street",     node.street || "—"],
      ["City, State", (node.city || node.state)
                        ? [node.city, node.state].filter(Boolean).join(", ")
                        : "—"],
      ["Altitude",   altStr],
      ["Packets",    node.total_packets],
      ["Last heard", ts],
      ["First heard", tsFirst],
    ];
    const trs = rows.map(([k, v]) =>
      `<tr><td style="color:#888;padding-right:4px">${k}</td><td>${v}</td></tr>`
    ).join("");
    return `<table style="font-size:10px;font-family:monospace;line-height:1.4">${trs}</table>`
      + `<div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px">`
      + `<button class="nm-text-btn nm-traffic-btn" data-node-id="${node.node_id}" style="font-size:10px;padding:2px 8px">See Traffic</button>`
      + `<button class="nm-btn-danger nm-node-delete-btn" data-node-id="${node.node_id}" style="font-size:10px;padding:2px 8px">Delete Node</button>`
      + `</div>`;
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
      const hopsAway = (n.hops_away != null && Number.isFinite(n.hops_away)) ? n.hops_away : null;
      return hopsAway != null && hopsAway <= _maxHopsFilter;
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
      const hopsAway = (n.hops_away != null && Number.isFinite(n.hops_away)) ? n.hops_away : null;
      return hopsAway != null && hopsAway <= _maxHopsFilter;
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

  function closePopups() {
    if (_map) _map.closePopup();
    _dismissLabel();
  }

  return {
    init,
    invalidate,
    highlightNode,
    flyToNode,
    hasMarker,
    closePopups,
    buildNodePopupHtml: _popupHtml,
  };
})();
