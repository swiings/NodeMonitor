/**
 * timeline.js — Chart.js packet activity bar chart.
 *
 * Listens for:
 *   "nm:timeline-updated" — updates chart data
 *   "nm:settings-loaded"  — captures cfg for y-axis and accent settings
 */

"use strict";

const NMTimeline = (() => {
  let _chart = null;
  let _cfg = {};

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
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (items) => {
                const label = items[0]?.label || "";
                return label;
              },
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
  }

  // ── Update ────────────────────────────────────────────────────

  function _update(data) {
    if (!_chart) return;
    const { buckets, time_scale_hours } = data;
    const tlCfg = _cfg.timeline || {};

    const labels = _buildLabels(buckets, time_scale_hours);
    const datasets = _buildDatasets(buckets, tlCfg);

    // Y-axis max
    const yMax = tlCfg.y_axis_mode === "fixed" ? (tlCfg.y_axis_max || 30) : undefined;
    _chart.options.scales.y.max = yMax;

    // Thin out x-axis ticks based on time scale
    _chart.options.scales.x.ticks.maxTicksLimit = time_scale_hours * 2 + 1;

    _chart.data.labels = labels;
    _chart.data.datasets = datasets;
    _chart.update("none");
  }

  // ── Labels ────────────────────────────────────────────────────

  function _buildLabels(buckets, timeScaleHours) {
    return buckets.map((b) => {
      const d = new Date(b.minute_ts * 1000);
      const h = d.getHours().toString().padStart(2, "0");
      const m = d.getMinutes().toString().padStart(2, "0");
      return `${h}:${m}`;
    });
  }

  // ── Datasets ──────────────────────────────────────────────────

  function _buildDatasets(buckets, tlCfg) {
    const isEink = document.body.classList.contains("theme-eink");

    const allColor  = isEink ? "rgba(0,0,0,0.6)" : "rgba(66,133,244,0.75)";
    const msgColor  = isEink ? "rgba(0,0,0,0.9)" : "rgba(220,50,50,0.9)";
    const chanColor = isEink ? "#555555"          : "#d29922";

    const datasets = [
      {
        label: "All Packets",
        data: buckets.map(b => b.total),
        backgroundColor: allColor,
        borderWidth: 0,
        barPercentage: 0.95,
        categoryPercentage: 1.0,
        stack: "pkts",
      },
    ];

    if (tlCfg.message_accent !== false) {
      datasets.push({
        label: "Messages",
        data: buckets.map(b => b.messages),
        backgroundColor: msgColor,
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

  return { init };
})();
