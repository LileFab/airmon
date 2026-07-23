"use strict";

// digits = décimales affichées (courbe/tuile/infobulle) ; la base garde la précision complète.
// yDigits (si défini) force l'axe Y à cette précision, sinon format par défaut.
const METRICS = [
  { key: "temperature",    label: "Température",       unit: "°C",  digits: 1, yDigits: 1 },
  { key: "humidity",       label: "Humidité",          unit: "%",   digits: 1, yDigits: 1 },
  { key: "pressure",       label: "Pression",          unit: "hPa", digits: 1, yDigits: 1 },
  { key: "gas_resistance", label: "Résistance de gaz", unit: "Ω",   digits: 0 },
  { key: "air_quality",    label: "Indice air",        unit: "/100",digits: 1 },
];

let currentRange = "24h";
let charts = {};
let lastPoints = [];

function cssVar(name) {
  return getComputedStyle(document.body).getPropertyValue(name).trim();
}
function metricColor(key) { return cssVar("--c-" + key); }
function withAlpha(hex, a) {
  const h = hex.replace("#", "");
  const n = parseInt(h.length === 3 ? h.split("").map(c => c + c).join("") : h, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

function fmtTime(ms, range) {
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, "0");
  if (range === "7d" || range === "30d" || range === "all") {
    return `${p(d.getDate())}/${p(d.getMonth() + 1)}`;
  }
  return `${p(d.getHours())}:${p(d.getMinutes())}`;
}

// Trait vertical au survol (crosshair).
const crosshair = {
  id: "crosshair",
  afterDraw(chart) {
    const active = chart.tooltip?.getActiveElements?.();
    if (!active || !active.length) return;
    const x = active[0].element.x;
    const { top, bottom } = chart.chartArea;
    const ctx = chart.ctx;
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    ctx.lineWidth = 1;
    ctx.strokeStyle = cssVar("--muted");
    ctx.setLineDash([3, 3]);
    ctx.stroke();
    ctx.restore();
  },
};

function makeChart(metric) {
  const ctx = document.getElementById("c-" + metric.key).getContext("2d");
  const color = metricColor(metric.key);
  return new Chart(ctx, {
    type: "line",
    data: {
      datasets: [{
        label: metric.label,
        data: [],
        borderColor: color,
        backgroundColor: withAlpha(color, 0.12),
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 4,
        pointHoverBackgroundColor: color,
        pointHoverBorderColor: cssVar("--surface"),
        pointHoverBorderWidth: 2,
        tension: 0.25,
        fill: true,
        spanGaps: true,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      parsing: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: {
          type: "linear",
          grid: { display: false },
          border: { color: cssVar("--axis") },
          ticks: {
            color: cssVar("--muted"),
            font: { family: "system-ui, sans-serif", size: 11 },
            maxRotation: 0,
            autoSkipPadding: 24,
            callback: (v) => fmtTime(v, currentRange),
          },
        },
        y: {
          grid: { color: cssVar("--grid"), drawTicks: false },
          border: { display: false },
          ...(metric.key === "air_quality" ? { min: 0, max: 100 } : {}),
          ticks: {
            color: cssVar("--muted"),
            font: { family: "system-ui, sans-serif", size: 11 },
            padding: 8,
            // Axe Y au dixième pour temp/humidité/pression (évite les centièmes/millièmes).
            ...(metric.yDigits != null
              ? { precision: metric.yDigits, callback: (v) => Number(v).toFixed(metric.yDigits) }
              : {}),
          },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          displayColors: false,
          backgroundColor: cssVar("--surface"),
          titleColor: cssVar("--text-secondary"),
          bodyColor: cssVar("--text-primary"),
          borderColor: cssVar("--border"),
          borderWidth: 1,
          padding: 10,
          callbacks: {
            title: (items) => {
              const d = new Date(items[0].parsed.x);
              return d.toLocaleString("fr-FR", {
                day: "2-digit", month: "2-digit",
                hour: "2-digit", minute: "2-digit",
              });
            },
            label: (item) => {
              const v = item.parsed.y;
              return v == null ? "—"
                : `${v.toFixed(metric.digits)} ${metric.unit}`.replace("/100", "/100");
            },
          },
        },
      },
    },
    plugins: [crosshair],
  });
}

function initCharts() {
  METRICS.forEach((m) => { charts[m.key] = makeChart(m); });
}

function updateCharts(points) {
  lastPoints = points;
  METRICS.forEach((m) => {
    const chart = charts[m.key];
    // Courbe lissée : données pleines. L'axe Y et l'infobulle sont formatés au dixième.
    chart.data.datasets[0].data = points.map((p) => ({ x: p.t, y: p[m.key] }));
    chart.update();
  });
}

function renderTiles(latest) {
  const wrap = document.getElementById("tiles");
  if (!latest) { wrap.innerHTML = ""; return; }
  wrap.innerHTML = METRICS.map((m) => {
    const v = latest[m.key];
    const shown = v == null ? "—" : Number(v).toFixed(m.digits);
    return `<div class="tile">
      <div class="label"><span class="dot" style="background:${metricColor(m.key)}"></span>${m.label}</div>
      <div class="value">${shown}<small>${m.unit}</small></div>
    </div>`;
  }).join("");
}

async function refresh() {
  try {
    const [dataRes, latestRes] = await Promise.all([
      fetch(`/api/data?range=${currentRange}`),
      fetch(`/api/latest`),
    ]);
    const data = await dataRes.json();
    const latest = await latestRes.json();

    updateCharts(data.points || []);
    renderTiles(latest.latest);

    const status = document.getElementById("status");
    if (latest.latest) {
      const ago = new Date(latest.latest.t).toLocaleTimeString("fr-FR");
      status.textContent = `Dernière mesure à ${ago}`;
      if (latest.latest.air_quality == null) {
        status.textContent += " · chauffe du capteur en cours…";
      }
    } else {
      status.textContent = "En attente de la première mesure…";
    }
    document.getElementById("count").textContent =
      `${(latest.count || 0).toLocaleString("fr-FR")} mesures`;
  } catch (e) {
    document.getElementById("status").textContent = "Erreur de connexion au serveur";
  }
}

function restyleCharts() {
  // Réapplique les couleurs (thème) sur les graphes existants.
  Object.entries(charts).forEach(([key, chart]) => {
    const color = metricColor(key);
    const ds = chart.data.datasets[0];
    ds.borderColor = color;
    ds.backgroundColor = withAlpha(color, 0.12);
    ds.pointHoverBackgroundColor = color;
    ds.pointHoverBorderColor = cssVar("--surface");
    const s = chart.options.scales;
    s.x.border.color = cssVar("--axis");
    s.x.ticks.color = cssVar("--muted");
    s.y.grid.color = cssVar("--grid");
    s.y.ticks.color = cssVar("--muted");
    const tt = chart.options.plugins.tooltip;
    tt.backgroundColor = cssVar("--surface");
    tt.titleColor = cssVar("--text-secondary");
    tt.bodyColor = cssVar("--text-primary");
    tt.borderColor = cssVar("--border");
    chart.update();
  });
}

document.getElementById("ranges").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-range]");
  if (!btn) return;
  document.querySelectorAll("#ranges button").forEach((b) => b.classList.remove("active"));
  btn.classList.add("active");
  currentRange = btn.dataset.range;
  refresh();
});

document.getElementById("theme-toggle").addEventListener("click", () => {
  const cur = document.documentElement.getAttribute("data-theme");
  const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const next = cur ? (cur === "dark" ? "light" : "dark") : (dark ? "light" : "dark");
  document.documentElement.setAttribute("data-theme", next);
  restyleCharts();
  renderTiles(lastPoints.length ? lastPoints[lastPoints.length - 1] : null);
});

initCharts();
refresh();
setInterval(refresh, 30000);
