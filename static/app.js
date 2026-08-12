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

// Courbe de température extérieure (Open-Meteo) superposée au graphe température.
const OUTDOOR_LABEL = "Extérieur · Lyon";
let outdoorEnabled = localStorage.getItem("outdoorEnabled") !== "false"; // défaut : affichée

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
              const val = v == null ? "—" : `${v.toFixed(metric.digits)} ${metric.unit}`;
              // Graphe multi-séries (température int./ext.) : préfixer par le nom de série.
              return item.chart.data.datasets.length > 1 ? `${item.dataset.label} : ${val}` : val;
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
  addOutdoorSeries();
}

// Ajoute la série "extérieur" (2ᵉ dataset) au graphe température : trait bleu tireté,
// sans remplissage. Le dataset intérieur est relabellisé "Intérieur" pour l'infobulle.
function addOutdoorSeries() {
  const chart = charts.temperature;
  chart.data.datasets[0].label = "Intérieur";
  const color = cssVar("--c-outdoor");
  chart.data.datasets.push({
    label: OUTDOOR_LABEL,
    data: [],
    borderColor: color,
    backgroundColor: "transparent",
    borderWidth: 2,
    borderDash: [5, 4],
    pointRadius: 0,
    pointHoverRadius: 4,
    pointHoverBackgroundColor: color,
    pointHoverBorderColor: cssVar("--surface"),
    pointHoverBorderWidth: 2,
    tension: 0.3,
    fill: false,
    spanGaps: true,
    hidden: !outdoorEnabled,
  });
  chart.update();
}

function updateOutdoor(points) {
  const chart = charts.temperature;
  const ds = chart.data.datasets[1];
  if (!ds) return;
  ds.data = points.map((p) => ({ x: p.t, y: p.temperature }));
  chart.update();
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

function fmtUptime(seconds) {
  if (seconds == null) return null;
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d} j ${h} h`;
  if (h > 0) return `${h} h ${m} min`;
  return `${m} min`;
}

async function refreshSystem() {
  try {
    const res = await fetch(`/api/system`);
    const sys = await res.json();
    const parts = [];
    if (sys.cpu_temp != null) parts.push(`Pi ${sys.cpu_temp.toFixed(1)} °C`);
    const uptime = fmtUptime(sys.uptime_seconds);
    if (uptime) parts.push(`up ${uptime}`);
    document.getElementById("pi-status").textContent = parts.join(" · ");
  } catch (e) {
    document.getElementById("pi-status").textContent = "";
  }
}

async function refresh() {
  try {
    const [dataRes, latestRes, outdoorRes] = await Promise.all([
      fetch(`/api/data?range=${currentRange}`),
      fetch(`/api/latest`),
      fetch(`/api/outdoor?range=${currentRange}`),
    ]);
    const data = await dataRes.json();
    const latest = await latestRes.json();
    const outdoor = await outdoorRes.json();

    updateCharts(data.points || []);
    updateOutdoor(outdoor.points || []);
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
  refreshSystem();
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
  // Série extérieure (2ᵉ dataset du graphe température) : sa couleur ne suit pas la clé.
  const outdoor = charts.temperature?.data.datasets[1];
  if (outdoor) {
    const oc = cssVar("--c-outdoor");
    outdoor.borderColor = oc;
    outdoor.pointHoverBackgroundColor = oc;
    outdoor.pointHoverBorderColor = cssVar("--surface");
    charts.temperature.update();
  }
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

// Légende cliquable "Extérieur" : affiche/masque la courbe et mémorise le choix.
const outdoorToggle = document.getElementById("outdoor-toggle");

function syncOutdoorToggle() {
  outdoorToggle.classList.toggle("off", !outdoorEnabled);
  outdoorToggle.setAttribute("aria-pressed", String(outdoorEnabled));
  const ds = charts.temperature.data.datasets[1];
  if (ds) { ds.hidden = !outdoorEnabled; charts.temperature.update(); }
}

outdoorToggle.addEventListener("click", () => {
  outdoorEnabled = !outdoorEnabled;
  localStorage.setItem("outdoorEnabled", String(outdoorEnabled));
  syncOutdoorToggle();
});

initCharts();
syncOutdoorToggle();
refresh();
setInterval(refresh, 30000);
