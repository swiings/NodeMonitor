/**
 * timeline.js — Chart.js packet activity bar chart.
 *
 * Listens for:
 *   "nm:timeline-updated" — updates chart data
 *   "nm:settings-loaded"  — captures cfg for y-axis and accent settings
 */

"use strict";

const NMTimeline = (() => {
  let _chart    = null;
  let _cfg      = {};
  let _yHighWater = 0;  // running max; only grows, never shrinks — keeps scale stable

  const MAX_SLIDER_MINUTES = 480;  // practical upper cap

  // Portnum display order (bottom of stack → top) and colors.
  // Dark palette: muted, low-brightness versions of each hue.
  const _PORTNUM_STACK = [
    { key: "__OTHER__",       color: "rgba(44,51,60,0.95)",   eink: "rgba(0,0,0,0.3)"  },
    { key: "NODEINFO_APP",    color: "rgba(100,48,152,0.9)",  eink: "rgba(0,0,0,0.5)"  },
    { key: "TELEMETRY_APP",   color: "rgba(26,76,148,0.9)",   eink: "rgba(0,0,0,0.6)"  },
    { key: "POSITION_APP",    color: "rgba(24,100,46,0.9)",   eink: "rgba(0,0,0,0.7)"  },
    { key: "UNKNOWN",         color: "rgba(112,78,10,0.9)",   eink: "rgba(80,80,80,0.7)"},
    { key: "TEXT_MESSAGE_APP",color: "rgba(138,30,26,0.9)",   eink: "rgba(0,0,0,0.9)"  },
  ];
  const _KNOWN_PORTNUMS = new Set(_PORTNUM_STACK.map(p => p.key).filter(k => k !== "__OTHER__"));

  // ── Init ──────────────────────────────────────────────────────

  function init(canvasId) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext("2d");

    _chart = new Chart(ctx, {
      type: "bar",
      data: { labels: [], datasets: [] },
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        // index mode: hovering any segment of a bar shows one tooltip for the whole column
        interaction: {
          mode: "index",
          intersect: false,
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            // Filter out zero-count rows and the channel util line (shown separately)
            filter: (item) => item.dataset.yAxisID !== "yChan" && item.parsed.y > 0,
            callbacks: {
              title: (items) => items[0]?.label || "",
              label: (item) => ` ${item.dataset.label}: ${item.parsed.y}`,
            },
          },
        },
        scales: {
          x: {
            stacked: true,
            ticks: {
              maxRotation: 0,
              font: { size: 10, family: "Courier New, monospace" },
              color: "#6e7681",
            },
            grid: { display: false },
            border: { color: "#30363d" },
          },
          y: {
            stacked: true,
            beginAtZero: true,
            ticks: {
              stepSize: 1,
              font: { size: 10, family: "Courier New, monospace" },
              color: "#6e7681",
            },
            grid: { color: "#21262d" },
            border: { color: "#30363d" },
          },
          yChan: {
            position: "right",
            beginAtZero: true,
            min: 0,
            max: 100,
            ticks: {
              font: { size: 9, family: "Courier New, monospace" },
              color: "#d29922",
              callback: (v) => `${v}%`,
              maxTicksLimit: 5,
            },
            grid: { display: false },
            border: { color: "#30363d" },
          },
        },
      },
    });

    document.addEventListener("nm:timeline-updated", (e) => _update(e.detail));
    document.addEventListener("nm:settings-loaded",  (e) => { _cfg = e.detail.cfg; });

    _initTimeSlider();
  }

  // ── Time window slider (left edge of panel) ───────────────────

  function _initTimeSlider() {
    const slider = document.getElementById("nm-time-slider");
    if (!slider) return;

    // Restore from settings
    const tsm = (_cfg.timeline || {}).time_scale_minutes || 360;
    slider.value = tsm;

    slider.addEventListener("input", () => {
      _applySliderValue(parseInt(slider.value));
    });
  }

  let _sliderDebounce = null;
  function _applySliderValue(minutes) {
    clearTimeout(_sliderDebounce);
    _sliderDebounce = setTimeout(() => {
      fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ timeline: { time_scale_minutes: minutes } }),
      }).then(r => r.json()).then(d => {
        if (d.updated) {
          document.dispatchEvent(new CustomEvent("nm:settings-loaded", { detail: { cfg: d.updated } }));
          // Immediately trigger a data refresh
          document.dispatchEvent(new CustomEvent("nm:request-poll"));
        }
      });
    }, 200);
  }

  // ── Update ────────────────────────────────────────────────────

  function _update(data) {
    if (!_chart) return;
    const { buckets, time_scale_minutes, session_minutes } = data;
    const tlCfg = _cfg.timeline || {};

    // Sync slider range and value from API response
    const slider = document.getElementById("nm-time-slider");
    if (slider) {
      const sliderMax = Math.min(MAX_SLIDER_MINUTES, session_minutes || MAX_SLIDER_MINUTES);
      slider.max   = sliderMax;
      slider.value = Math.min(time_scale_minutes || 360, sliderMax);
    }

    const labels   = _buildLabels(buckets);
    const datasets = _buildDatasets(buckets, tlCfg);

    // Y-axis max — stable: never let the scale shrink between refreshes.
    // If settings specify a fixed ceiling, respect that; otherwise ratchet up only.
    if (tlCfg.y_axis_mode === "fixed") {
      _chart.options.scales.y.max = tlCfg.y_axis_max || 30;
    } else {
      const dataMax = Math.max(0, ...buckets.map(b => b.total || 0));
      _yHighWater = Math.max(_yHighWater, dataMax);
      _chart.options.scales.y.max = _yHighWater || undefined;
    }

    // Thin out x-axis ticks: ~1 tick per 30-minute window
    const windowMins = time_scale_minutes || 360;
    _chart.options.scales.x.ticks.maxTicksLimit = Math.max(3, Math.round(windowMins / 30) + 1);

    _chart.data.labels   = labels;
    _chart.data.datasets = datasets;
    _chart.update("none");

    // Align UTILIZATION label above the right y-axis tick text.
    // After chart.update() the chartArea geometry is final — use it directly.
    _alignUtilLabel();
  }

  // ── Labels ────────────────────────────────────────────────────

  function _buildLabels(buckets) {
    return buckets.map((b) => {
      const d = new Date(b.minute_ts * 1000);
      const h = d.getHours().toString().padStart(2, "0");
      const m = d.getMinutes().toString().padStart(2, "0");
      return `${h}:${m}`;
    });
  }

  // ── Datasets ──────────────────────────────────────────────────

  function _buildDatasets(buckets, tlCfg) {
    const isEink    = document.body.classList.contains("theme-eink");
    const chanColor = isEink ? "#555555" : "#d29922";

    const datasets = [];

    // One stacked bar dataset per portnum in the display order (bottom → top)
    for (const { key, color, eink } of _PORTNUM_STACK) {
      datasets.push({
        label: key === "__OTHER__" ? "Other" : key.replace(/_APP$/, "").replace(/_/g, " "),
        data: buckets.map(b => {
          const byPortnum = b.by_portnum || {};
          if (key === "__OTHER__") {
            // Sum anything not in the known set
            return Object.entries(byPortnum)
              .filter(([k]) => !_KNOWN_PORTNUMS.has(k))
              .reduce((s, [, v]) => s + v, 0);
          }
          return byPortnum[key] || 0;
        }),
        backgroundColor: isEink ? eink : color,
        borderWidth: 0,
        barPercentage: 0.95,
        categoryPercentage: 1.0,
        stack: "pkts",
      });
    }

    // Channel utilization line — only if any data exists
    const chanData = buckets.map(b => b.channel_util ?? null);
    if (chanData.some(v => v !== null)) {
      datasets.push({
        type: "line",
        label: "Chan Util %",
        data: chanData,
        yAxisID: "yChan",
        borderColor: chanColor,
        borderWidth: 1.5,
        pointRadius: 0,
        pointHoverRadius: 3,
        spanGaps: false,
        tension: 0.3,
        fill: false,
        order: -1,  // draw on top of bars
      });
    }

    return datasets;
  }

  // ── UTILIZATION label alignment ───────────────────────────────
  // Right-aligns the label to sit above the yChan axis tick text.
  // Chart.js exposes exact pixel geometry after each render:
  //   _chart.chartArea.right = right edge of the plot area (where bars end)
  //   _chart.width           = full canvas pixel width
  // The right axis occupies [chartArea.right .. chart.width].
  // The label bar's right edge is flush with the panel edge.
  // The image sits between the panel edge and the canvas-wrap.
  // So: marginRight = img.offsetWidth + (canvas.offsetWidth - chart.width)
  //                 + (chart.width - chart.chartArea.right)
  //               = img.offsetWidth + canvas.offsetWidth - chart.chartArea.right
  function _alignUtilLabel() {
    const utilLabel = document.getElementById("nm-timeline-util-label");
    if (!utilLabel || !_chart || !_chart.chartArea) return;

    const img    = document.getElementById("nm-timeline-img");
    const wrap   = document.getElementById("nm-timeline-canvas-wrap");
    const imgW   = (img && img.offsetWidth) ? img.offsetWidth : 0;
    const wrapW  = wrap ? wrap.offsetWidth : _chart.width;

    // Distance from the label bar's right edge to where yChan axis text aligns
    const marginRight = imgW + (wrapW - _chart.chartArea.right);
    utilLabel.style.marginRight = `${Math.max(0, marginRight)}px`;
  }

  return { init };
})();
