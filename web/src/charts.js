// Plotly drawing for every chart except the telemetry rows (TelemetryChart.jsx):
// stint trends, the metrics charts, and the track maps.
import { css, lapColor, LAP_DASHES, nearestIndex, PLOT_CONFIG, Plotly } from "./chartUtils.js";

export const gateColor = (g) => css(`--gate-${g}`);

// Card background, quiet grid, monospace numbers - shared by every chart here.
// Colours come from the theme's CSS variables when the chart draws (see Plot.jsx
// for the redraw when the theme changes).
const base = (height, extra = {}) => ({
  height, margin: { l: 48, r: 12, t: 12, b: 36 },
  paper_bgcolor: css("--card"), plot_bgcolor: css("--card"),
  font: { family: css("--mono"), size: 10, color: css("--text-3") },
  hoverlabel: { bgcolor: css("--raised"), bordercolor: css("--border-strong"), font: { family: css("--mono"), color: css("--text") } },
  legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", font: { color: css("--text-2") } },
  ...extra,
});
const axis = (extra = {}) => ({ gridcolor: css("--border"), linecolor: css("--border"), zerolinecolor: css("--border-strong"), ...extra });

// ---------- stint trends: one small chart per measure, x = lap number ----------
// series: [{ name, values: [...per lap], slot }]. Marker shape shows what kind of lap it was:
//   ● full valid lap   ✕ invalid lap (a small red mark on the x axis too)
//   ○ out lap / unfinished lap: grey and not joined by the line (only passed in when they're counted)
// A timed lap the lap filter leaves out (too slow, first/last) is grey instead of the series colour.
export function drawTrend(el, unit, laps, series, { bars = false } = {}) {
  const symbol = laps.map((l) => (l.kind !== "timed" ? "circle-open" : l.invalid ? "x" : "circle"));
  const text = laps.map((l) => {
    const why = l.excluded && (l.kind === "timed" ? `left out: ${l.excluded}` : l.excluded);
    const bits = [l.invalid && "invalid", why].filter(Boolean);
    return bits.length ? ` (${bits.join(", ")})` : "";
  });
  const greyed = laps.map((l) => l.kind === "timed" && !!l.excluded);
  const colors = (slot) => greyed.map((g) => (g ? css("--text-3") : lapColor(slot)));
  const x = laps.map((l) => l.number);
  const partial = laps.map((l) => l.kind !== "timed"); // out lap / unfinished lap
  const grey = css("--text-3");
  const hover = (name) => `%{y:.3~f} ${unit}%{customdata}<extra>${name}</extra>`;
  const traces = series.flatMap((s) => (bars ? [{
    type: "bar", x, y: s.values, name: s.name,
    marker: { color: colors(s.slot).map((c, i) => (partial[i] ? grey : c)), opacity: laps.map((l) => (l.kind === "timed" ? 1 : 0.5)) },
    customdata: text, hovertemplate: hover(s.name),
  }] : [
    // The line only joins full laps: an out or unfinished lap leaves a gap...
    {
      type: "scatter", mode: "lines+markers", x, y: s.values.map((v, i) => (partial[i] ? null : v)), name: s.name,
      connectgaps: false, legendgroup: s.name,
      line: { color: lapColor(s.slot), width: 1.5 },
      marker: { symbol, size: 7, color: colors(s.slot), line: { width: 1.5, color: colors(s.slot) } },
      customdata: text, hovertemplate: hover(s.name),
    },
    // ...and is drawn on its own as a grey ring
    {
      type: "scatter", mode: "markers", name: s.name, legendgroup: s.name, showlegend: false,
      x: x.filter((_, i) => partial[i]), y: s.values.filter((_, i) => partial[i]),
      marker: { symbol: "circle-open", size: 7, color: grey, line: { width: 1.5, color: grey } },
      customdata: text.filter((_, i) => partial[i]), hovertemplate: hover(s.name),
    },
  ]));
  // A short red mark on the x axis under every invalid lap
  const invalidMarks = laps.filter((l) => l.kind === "timed" && l.invalid).map((l) => ({
    type: "line", xref: "x", yref: "paper", x0: l.number, x1: l.number, y0: 0, y1: 0.08, line: { color: css("--bad"), width: 3 },
  }));
  Plotly.react(el, traces, base(200, {
    margin: { l: 48, r: 12, t: series.length > 1 ? 26 : 10, b: 30 },
    xaxis: axis({ dtick: 1, zeroline: false }),
    yaxis: axis({ zeroline: false, rangemode: bars ? "tozero" : "normal" }),
    hovermode: "x unified", showlegend: series.length > 1, bargap: 0.35, shapes: invalidMarks,
    legend: { orientation: "h", x: 1, xanchor: "right", y: 1.02, yanchor: "bottom", font: { color: css("--text-2") } },
  }), { ...PLOT_CONFIG, displayModeBar: false });
}

// ---------- metrics page ----------
// Track surface from the AC track files (when the server has them)
function tarmac(outline) {
  if (!outline) return [];
  const edge = (x, y) => ({ type: "scatter", mode: "lines", x, y, hoverinfo: "skip", showlegend: false, line: { color: css("--track-edge"), width: 1.2 } });
  return [
    { type: "scatter", mode: "lines", hoverinfo: "skip", showlegend: false, fill: "toself", fillcolor: css("--track-surface"), line: { width: 0 },
      // left edge all the way round, then the right edge backwards -> a ring
      x: [...outline.left_x, null, ...outline.right_x.slice().reverse()], y: [...outline.left_y, null, ...outline.right_y.slice().reverse()] },
    edge(outline.left_x, outline.left_y), edge(outline.right_x, outline.right_y),
  ];
}
const mapAxes = { xaxis: { visible: false }, yaxis: { visible: false, scaleanchor: "x", scaleratio: 1 } }; // 1 m across = 1 m up

const SPEED_STEPS = 16; // speed colouring is drawn as this many bands of colour

// Colour at position t (0..1) along a list of hex colours, blending between neighbours
function rampColor(ramp, t) {
  const pos = Math.min(Math.max(t, 0), 1) * (ramp.length - 1);
  const i = Math.min(Math.floor(pos), ramp.length - 2), f = pos - i;
  const rgb = (hex) => [1, 3, 5].map((o) => parseInt(hex.slice(o, o + 2), 16));
  const [a, b] = [rgb(ramp[i]), rgb(ramp[i + 1])];
  return `rgb(${a.map((v, k) => Math.round(v + f * (b[k] - v))).join(",")})`;
}

// Pedal colouring for racing lines: what the driver is doing at each point.
// Soft red / off-white / soft green on the dark theme; stronger on the light one (tokens.css).
export const PEDALS = [
  { name: "Braking", token: "--pedal-brake" },
  { name: "Coasting", token: "--pedal-coast" },
  { name: "Throttle", token: "--pedal-throttle" },
];
const PEDAL_ON = 2; // % - a pedal above this counts as pressed (same as the metrics default)
const pedalState = (throttle, brake) => (brake > PEDAL_ON ? 0 : throttle > PEDAL_ON ? 2 : 1);

// Split a line into one piece per state (speed band, pedal...) so each piece can
// have its own colour. Plotly can't change a line's colour along its length,
// so each state becomes its own trace; neighbouring stretches share their end
// point so the line has no gaps. Returns [{ x, y, i }] per state, where i holds
// the original point index (null at breaks) for hover text.
function splitByState(x, y, stateOf, nStates) {
  const out = Array.from({ length: nStates }, () => ({ x: [], y: [], i: [] }));
  for (let k = 0; k < x.length; k++) {
    const s = stateOf(k), next = k + 1 < x.length ? stateOf(k + 1) : null;
    const piece = out[s];
    piece.x.push(x[k]); piece.y.push(y[k]); piece.i.push(k);
    if (next !== null && next !== s) {
      // carry on one point into the next stretch so they join, then break this piece
      piece.x.push(x[k + 1]); piece.y.push(y[k + 1]); piece.i.push(k + 1);
      piece.x.push(null); piece.y.push(null); piece.i.push(null);
    }
  }
  return out;
}

// The reference lap's line, each stretch coloured by its gate
export function drawGateMap(el, m, outline) {
  const gm = m.gate_map;
  const traces = tarmac(outline);
  m.gates.forEach((name, g) => {
    const x = [], y = [], d = [];
    for (let k = 0; k < gm.gate.length; k++) {
      const on = gm.gate[k] === g, next = gm.gate[k + 1] === g;
      if (on || next) { x.push(gm.x[k]); y.push(gm.y[k]); d.push(gm.distance[k]); } // include the joining point
      if (on && !next) { x.push(null); y.push(null); d.push(null); }
    }
    traces.push({ type: "scatter", mode: "lines", x, y, customdata: d, name, line: { color: gateColor(g), width: 5 },
      hovertemplate: `${name} · %{customdata:.0f} m<extra></extra>` });
  });
  Plotly.react(el, traces, base(360, { margin: { l: 6, r: 6, t: 28, b: 6 }, ...mapAxes, hovermode: "closest" }),
    { ...PLOT_CONFIG, scrollZoom: true });
}

// Lateral vs longitudinal G. Lines = each lap's grip envelope (furthest point in every 10° direction).
export function drawGG(el, m, laps, colourBy) {
  const traces = [];
  for (const r of [1, 2, 3]) { // guide circles
    const a = Array.from({ length: 73 }, (_, i) => (i * 5 * Math.PI) / 180);
    traces.push({ type: "scatter", mode: "lines", x: a.map((t) => r * Math.cos(t)), y: a.map((t) => r * Math.sin(t)),
      hoverinfo: "skip", showlegend: false, line: { color: css("--border-strong"), width: 1 } });
  }
  const point = (lap, i, k) => ({ x: lap.gg.lat[k], y: lap.gg.long[k], text: `${laps[i].shortLabel} · ${m.gates[lap.gg.gate[k]]} · ${lap.gg.speed[k].toFixed(0)} kph` });
  const scatter = (pts, name, color) => ({
    type: "scattergl", mode: "markers", name, x: pts.map((p) => p.x), y: pts.map((p) => p.y), text: pts.map((p) => p.text),
    marker: { size: 4, color, opacity: 0.45 }, hovertemplate: "lat %{x:.2f} g · long %{y:.2f} g<br>%{text}<extra></extra>",
  });
  if (colourBy === "gate") {
    m.gates.forEach((name, g) => {
      const pts = m.laps.flatMap((lap, i) => lap.gg.gate.flatMap((gg, k) => (gg === g ? [point(lap, i, k)] : [])));
      if (pts.length) traces.push(scatter(pts, name, gateColor(g)));
    });
  } else {
    m.laps.forEach((lap, i) => traces.push(scatter(lap.gg.lat.map((_, k) => point(lap, i, k)), laps[i].shortLabel, lapColor(laps[i].slot))));
  }
  m.laps.forEach((lap, i) => { // envelopes
    const bins = new Array(36).fill(null);
    lap.gg.lat.forEach((x, k) => {
      const y = lap.gg.long[k], r = Math.hypot(x, y);
      const b = Math.floor(((Math.atan2(y, x) + Math.PI) / (2 * Math.PI)) * 36) % 36;
      if (!bins[b] || r > bins[b].r) bins[b] = { r, x, y };
    });
    const pts = bins.filter(Boolean);
    pts.push(pts[0]);
    traces.push({ type: "scatter", mode: "lines", x: pts.map((p) => p.x), y: pts.map((p) => p.y), name: `${laps[i].shortLabel} envelope`,
      line: { color: lapColor(laps[i].slot), width: 2, dash: LAP_DASHES[i % LAP_DASHES.length] }, hoverinfo: "skip" });
  });
  Plotly.react(el, traces, base(440, {
    margin: { l: 48, r: 12, t: 30, b: 40 },
    xaxis: axis({ title: { text: "Lateral G" }, range: [-3.2, 3.2] }),
    yaxis: axis({ title: { text: "Longitudinal G (+ accel, − braking)" }, scaleanchor: "x", scaleratio: 1 }),
    hovermode: "closest",
  }), PLOT_CONFIG);
}

// Grouped bars: balance in each gate, one bar per lap
export function drawBalance(el, m, laps) {
  const used = m.gates.map((_, g) => g).filter((g) => m.laps.some((l) => l.gates[g].balance_deg != null));
  const traces = m.laps.map((lap, i) => ({
    type: "bar", name: laps[i].shortLabel, x: used.map((g) => m.gates[g]), y: used.map((g) => lap.gates[g].balance_deg),
    marker: { color: lapColor(laps[i].slot) }, hovertemplate: `%{y:+.2f}°<extra>${laps[i].shortLabel}</extra>`,
  }));
  const note = (text, y, yanchor) => ({ text, xref: "paper", yref: "paper", x: 1, y, xanchor: "right", yanchor, showarrow: false, font: { size: 10, color: css("--text-3") } });
  Plotly.react(el, traces, base(440, {
    margin: { l: 48, r: 12, t: 30, b: 40 }, barmode: "group", bargap: 0.3,
    xaxis: axis(),
    // What balance is depends on the file (slip angles, or steering for iRacing): the API names the axis
    yaxis: axis({ title: { text: m.balance_axis || "Front − rear slip angle [°]" },
      zerolinecolor: css("--text-3"), zerolinewidth: 1.5 }),
    annotations: [note("understeer ↑", 1, "top"), note("oversteer ↓", 0, "bottom")],
    hovermode: "x unified",
  }), { ...PLOT_CONFIG, displayModeBar: false });
}

// Mechanical balance (est.) against lateral G, one line per lap (laps without tyre loads have no points).
// Flat = the springs and bars act linearly; a trend = bump stops, packers or a tyre lifting.
export function drawMech(el, m, laps) {
  const traces = m.laps.map((lap, i) => ({
    type: "scatter", mode: "lines+markers", name: laps[i].shortLabel,
    x: lap.mech_vs_lat_g.map((b) => b.lat_g), y: lap.mech_vs_lat_g.map((b) => b.front_pct),
    customdata: lap.mech_vs_lat_g.map((b) => b.points * 2), // STEP_M = 2 m per point
    line: { color: lapColor(laps[i].slot), width: 2, dash: LAP_DASHES[i % LAP_DASHES.length] }, marker: { size: 6 },
    hovertemplate: `%{y:.1f}% front at %{x:.2f} G · from %{customdata} m of cornering<extra>${laps[i].shortLabel}</extra>`,
  }));
  Plotly.react(el, traces, base(300, {
    margin: { l: 52, r: 12, t: 30, b: 40 },
    xaxis: axis({ title: { text: "Lateral G" } }),
    yaxis: axis({ title: { text: "Front share [%]" }, ticksuffix: "%" }),
    hovermode: "closest",
  }), { ...PLOT_CONFIG, displayModeBar: false });
}

// Damper histograms: which histograms each view shows, one panel each (see backend dampers.py SIGNALS)
export const DAMPER_VIEWS = {
  corners: { label: "Corners", signals: ["FL", "FR", "RL", "RR"], axis: "Damper speed [mm/s]   − rebound · + bump" },
  axles: { label: "Front v rear", signals: ["Front", "Rear"], axis: "Damper speed [mm/s]   − rebound · + bump" },
  sides: { label: "Left v right", signals: ["Left", "Right"], axis: "Damper speed [mm/s]   − rebound · + bump" },
  heave: { label: "Heave", signals: ["Front heave", "Rear heave"], axis: "Average of left and right [mm/s]   − extending · + compressing" },
  roll: { label: "Roll", signals: ["Front roll", "Rear roll"], axis: "Left minus right [mm/s]   + left compressing faster" },
};

// d: the /api/dampers response; sels: [{ label, slot }] per selection
// opts: { view, sharedY (one height scale for every panel), diff (each selection minus the reference) }
// Every panel shares the x axis (damper speed), so corners compare at a glance.
export function drawDampers(el, d, sels, { view, sharedY, diff }) {
  const signals = DAMPER_VIEWS[view].signals;
  const cols = 2, rows = Math.ceil(signals.length / cols);
  const edges = d.edges, w = edges[1] - edges[0];
  const x = edges.slice(0, -1).map((e) => e + w / 2); // bar centres
  // Hover text per bar; the end bars also hold everything beyond them
  const last = edges.length - 2;
  const range = edges.slice(0, -1).map((e, k) => (k === 0 ? `≤ ${edges[1]}` : k === last ? `≥ ${e}` : `${e} to ${edges[k + 1]}`));
  const { low_mm_s: low, high_mm_s: high } = d.settings;
  const gap = 0.07, rowGap = 0.16;
  const xDomain = (c) => [(c * (1 + gap)) / cols, (c * (1 + gap) + 1 - gap) / cols];
  const yDomain = (r) => { const h = (1 - rowGap * (rows - 1)) / rows, top = 1 - r * (h + rowGap); return [top - h, top]; };
  const ref = d.selections[0];

  const traces = [], axes = {}, shapes = [], annotations = [];
  signals.forEach((sig, p) => {
    const r = Math.floor(p / cols), c = p % cols, n = p ? String(p + 1) : "";
    const xa = `x${n}`, ya = `y${n}`;
    axes[`xaxis${n}`] = axis({ domain: xDomain(c), anchor: ya, zeroline: false, ...(p ? { matches: "x" } : {}),
      ...(r === rows - 1 ? { title: { text: DAMPER_VIEWS[view].axis, standoff: 4 } } : {}) });
    axes[`yaxis${n}`] = axis({ domain: yDomain(r), anchor: xa, rangemode: diff ? "normal" : "tozero",
      ...(sharedY && p ? { matches: "y" } : {}), ...(c === 0 ? { title: { text: diff ? "Δ % of time" : "% of time" } } : {}) });
    const k = d.signals.indexOf(sig);
    d.selections.forEach((s, i) => {
      if (diff && i === 0) return; // the reference is the zero line
      const pct = s.histograms[k].pct;
      const y = diff ? pct.map((v, b) => v - ref.histograms[k].pct[b]) : pct;
      const common = { x, y, xaxis: xa, yaxis: ya, name: sels[i].label, legendgroup: sels[i].label, showlegend: p === 0, customdata: range,
        hovertemplate: `%{customdata} mm/s · %{y:${diff ? "+" : ""}.1f}%<extra>${sig} · ${sels[i].label}</extra>` };
      // Overlaid bars blur into one colour, so when comparing, the reference is bars and the others are outlines
      if (!diff && i > 0) {
        traces.push({ ...common, type: "scatter", mode: "lines", line: { color: lapColor(sels[i].slot), width: 1.8, shape: "hvh" } });
      } else {
        traces.push({ ...common, type: "bar", marker: { color: lapColor(sels[i].slot) }, opacity: !diff && d.selections.length > 1 ? 0.6 : 0.9 });
      }
    });
    // Speed bands: dotted lines at the low / high limits either way, a solid line at zero
    for (const v of [-high, -low, low, high]) {
      shapes.push({ type: "line", xref: xa, yref: `${ya} domain`, x0: v, x1: v, y0: 0, y1: 1,
        line: { color: css("--border-strong"), width: 1, dash: "dot" } });
    }
    shapes.push({ type: "line", xref: xa, yref: `${ya} domain`, x0: 0, x1: 0, y0: 0, y1: 1, line: { color: css("--text-3"), width: 1 } });
    annotations.push({ text: sig, xref: `${xa} domain`, yref: `${ya} domain`, x: 0.01, y: 1, xanchor: "left", yanchor: "top",
      showarrow: false, font: { size: 11, color: css("--text-2") } });
  });

  Plotly.react(el, traces, base(rows > 1 ? 470 : 270, {
    margin: { l: 52, r: 12, t: 28, b: 44 }, barmode: diff ? "group" : "overlay", bargap: 0.05,
    hovermode: "closest", showlegend: true, shapes, annotations, ...axes,
    legend: { orientation: "h", x: 1, xanchor: "right", y: 1.01, yanchor: "bottom", font: { color: css("--text-2") } },
  }), { ...PLOT_CONFIG, displayModeBar: false });
}

// Camber optimiser: one panel per axle, the score for each static camber change tried.
// d: the /api/camber response; sels: [{ label, slot }] per selection. 0 = camber as run; the dot is the best.
export function drawCamber(el, d, sels) {
  const axles = ["Front", "Rear"], gap = 0.08;
  const traces = [], axes = {}, shapes = [], annotations = [];
  axles.forEach((axle, p) => {
    const n = p ? "2" : "", xa = `x${n}`, ya = `y${n}`;
    axes[`xaxis${n}`] = axis({ domain: [p * (0.5 + gap / 2), p * (0.5 + gap / 2) + 0.5 - gap / 2], anchor: ya, zeroline: false,
      title: { text: "Static camber change [deg]   − more negative", standoff: 4 } });
    axes[`yaxis${n}`] = axis({ anchor: xa, ticksuffix: "%", ...(p ? { matches: "y" } : { title: { text: "Camber grip score" } }) });
    d.selections.forEach((s, i) => {
      const a = s.axles.find((x) => x.axle === axle);
      if (!a?.score_pct.length) return;
      const common = { xaxis: xa, yaxis: ya, name: sels[i].label, legendgroup: sels[i].label };
      traces.push({ ...common, x: a.offsets_deg, y: a.score_pct, type: "scatter", mode: "lines", showlegend: p === 0,
        line: { color: lapColor(sels[i].slot), width: 2 }, hovertemplate: `%{x:+.1f}° · %{y:.2f}%<extra>${axle} · ${sels[i].label}</extra>` });
      traces.push({ ...common, x: [a.best_offset_deg], y: [a.best_pct], type: "scatter", mode: "markers", showlegend: false,
        marker: { color: lapColor(sels[i].slot), size: 9, line: { color: css("--card"), width: 1.5 } },
        hovertemplate: `best %{x:+.1f}° · %{y:.2f}%<extra>${axle} · ${sels[i].label}</extra>` });
    });
    shapes.push({ type: "line", xref: xa, yref: `${ya} domain`, x0: 0, x1: 0, y0: 0, y1: 1, line: { color: css("--text-3"), width: 1, dash: "dot" } });
    annotations.push({ text: axle, xref: `${xa} domain`, yref: `${ya} domain`, x: 0.01, y: 1, xanchor: "left", yanchor: "top",
      showarrow: false, font: { size: 11, color: css("--text-2") } });
  });
  Plotly.react(el, traces, base(290, {
    margin: { l: 56, r: 12, t: 28, b: 44 }, hovermode: "closest", showlegend: true, shapes, annotations, ...axes,
    legend: { orientation: "h", x: 1, xanchor: "right", y: 1.01, yanchor: "bottom", font: { color: css("--text-2") } },
  }), { ...PLOT_CONFIG, displayModeBar: false });
}

// ---------- track maps ----------
// opts: { surface, lines, lineColour: "lap" | "pedals", colouring: "dominance" | "speed" | "none", labels, big, height }
//   surface    - track edges + tarmac from the AC track files (if the server has them)
//   lines      - each lap's own racing line, outlined in dark so it reads on top of any colouring
//   lineColour - racing lines in each lap's colour, or by pedal: green throttle / white coast / red brake
//   colouring - colour the reference line by who won each mini-sector, or by speed
// The last trace is always a cursor dot that follows the mouse on the telemetry.
export function drawMap(el, cmp, laps, opts) {
  const m = cmp.map;
  const traces = opts.surface ? tarmac(cmp.outline) : [];
  const sectorRange = (s) => [Math.max(0, nearestIndex(m.distance, s.start_m) - 1), Math.min(m.distance.length - 1, nearestIndex(m.distance, s.end_m) + 1)];
  const width = opts.big ? 6 : 5;

  if (opts.colouring === "speed") {
    // slow -> fast; fast stands out most from the background (pale on dark, deep on light)
    const ramp = [1, 2, 3, 4].map((k) => css(`--speed-${k}`));
    const lo = Math.min(...m.speed), hi = Math.max(...m.speed);
    // A solid line (dots blurred together and broke up when zoomed in), in SPEED_STEPS bands of speed
    const band = (k) => Math.min(SPEED_STEPS - 1, Math.floor(((m.speed[k] - lo) / (hi - lo || 1)) * SPEED_STEPS));
    splitByState(m.x, m.y, band, SPEED_STEPS).forEach((p, b) => {
      if (!p.x.length) return;
      traces.push({ type: "scatter", mode: "lines", x: p.x, y: p.y, showlegend: false,
        customdata: p.i.map((k) => (k == null ? null : [m.distance[k], m.speed[k]])),
        line: { color: rampColor(ramp, (b + 0.5) / SPEED_STEPS), width },
        hovertemplate: `%{customdata[1]:.1f} kph · %{customdata[0]:.0f} m<extra>${laps[0].shortLabel}</extra>` });
    });
    // An invisible point, just to show the kph colour key
    traces.push({ type: "scatter", mode: "markers", x: [m.x[0]], y: [m.y[0]], hoverinfo: "skip", showlegend: false,
      marker: { size: 1, opacity: 0, color: [lo], cmin: lo, cmax: hi, showscale: true,
        colorscale: ramp.map((c, i) => [i / (ramp.length - 1), c]),
        colorbar: { title: { text: "kph" }, thickness: 8, len: 0.5, outlinewidth: 0, tickfont: { color: css("--text-2") } } } });
  } else if (opts.colouring === "dominance") {
    laps.forEach((lap, i) => {
      const x = [], y = [], text = [];
      cmp.sectors.filter((s) => s.winner === i).forEach((s) => {
        const [a, b] = sectorRange(s);
        for (let k = a; k <= b; k++) {
          x.push(m.x[k]); y.push(m.y[k]);
          text.push(`MS ${s.number} · ${lap.shortLabel} by ${s.margin_s.toFixed(3)} s`);
        }
        x.push(null); y.push(null); text.push(null);
      });
      traces.push({ type: "scatter", mode: "lines", x, y, text, name: `${lap.shortLabel} (fastest)`, legendgroup: "dom",
        line: { color: lapColor(lap.slot), width }, opacity: opts.lines ? 0.45 : 1, hovertemplate: "%{text}<extra></extra>" });
    });
  }

  if (opts.lines) {
    // Pedal colours need the throttle/brake that came with the racing line
    const pedals = opts.lineColour === "pedals" && cmp.lines.every((l) => l.throttle && l.brake);
    const lw = pedals ? (opts.big ? 2.6 : 2) : (opts.big ? 1.6 : 1.2);
    laps.forEach((lap, i) => {
      const l = cmp.lines[i];
      // Dark outline under each racing line (like roads on a map) so it stays
      // readable on top of the colour band, even where the colours are similar
      traces.push({ type: "scatter", mode: "lines", x: l.x, y: l.y, hoverinfo: "skip", showlegend: false,
        line: { color: css("--page"), width: lw + 2.4 } });
      if (!pedals) {
        traces.push({ type: "scatter", mode: "lines", x: l.x, y: l.y, customdata: l.distance, name: lap.shortLabel,
          line: { color: lapColor(lap.slot), width: lw },
          hovertemplate: `%{customdata:.0f} m<extra>${lap.shortLabel}</extra>` });
        return;
      }
      // Pedal colours: braking / coasting / throttle. Each lap keeps its own
      // dash style so several laps can still be told apart.
      const dash = LAP_DASHES[i % LAP_DASHES.length];
      splitByState(l.x, l.y, (k) => pedalState(l.throttle[k], l.brake[k]), PEDALS.length).forEach((p, s) => {
        if (!p.x.length) return;
        traces.push({ type: "scatter", mode: "lines", x: p.x, y: p.y, showlegend: false,
          customdata: p.i.map((k) => (k == null ? null : [l.distance[k], l.throttle[k], l.brake[k]])),
          line: { color: css(PEDALS[s].token), width: lw, dash },
          hovertemplate: `%{customdata[0]:.0f} m · throttle %{customdata[1]:.0f}% · brake %{customdata[2]:.0f}%<extra>${lap.shortLabel}</extra>` });
      });
    });
    if (pedals) { // key: one legend entry per pedal state, plus one per lap's dash style if several
      PEDALS.forEach((p) => traces.push({ type: "scatter", mode: "lines", x: [null], y: [null], name: p.name,
        line: { color: css(p.token), width: 3 }, hoverinfo: "skip" }));
      if (laps.length > 1) laps.forEach((lap, i) => traces.push({ type: "scatter", mode: "lines", x: [null], y: [null],
        name: lap.shortLabel, line: { color: css("--text-2"), width: 2, dash: LAP_DASHES[i % LAP_DASHES.length] }, hoverinfo: "skip" }));
    }
  }

  if (opts.labels) {
    const mid = cmp.sectors.map((s) => nearestIndex(m.distance, (s.start_m + s.end_m) / 2));
    traces.push({ type: "scatter", mode: "text", x: mid.map((k) => m.x[k]), y: mid.map((k) => m.y[k]),
      text: cmp.sectors.map((s) => String(s.number)), textfont: { size: 9, color: css("--text-2") },
      textposition: "top right", hoverinfo: "skip", showlegend: false });
  }
  // Start/finish square and an arrow showing the direction of travel
  const k = nearestIndex(m.distance, 30);
  traces.push({ type: "scatter", mode: "markers+text", x: [m.x[0], m.x[k]], y: [m.y[0], m.y[k]], hoverinfo: "skip", showlegend: false,
    marker: { symbol: ["square", "arrow"], angleref: "previous", size: [8, 13], color: css("--text") },
    text: ["S/F", ""], textposition: "bottom center", textfont: { size: 10, color: css("--text") } });
  // Cursor dot (moved by moveMapCursor)
  traces.push({ type: "scatter", mode: "markers", x: [null], y: [null], hoverinfo: "skip", showlegend: false,
    marker: { size: 11, color: css("--card"), line: { width: 3, color: css("--text") } } });

  Plotly.react(el, traces, base(opts.height ?? 440, {
    margin: { l: 6, r: 6, t: 6, b: 6 }, ...mapAxes,
    uirevision: "keep", // keep the user's zoom when layers change
    dragmode: opts.big ? "pan" : "zoom", hovermode: "closest",
    showlegend: laps.length > 1 || opts.lines, legend: { orientation: "h", x: 0, y: 1, font: { color: css("--text-2") } },
  }), { ...PLOT_CONFIG, scrollZoom: !!opts.big });
}

export function moveMapCursor(el, cmp, distance) {
  if (!el?.data) return;
  const k = distance == null ? null : nearestIndex(cmp.map.distance, distance);
  Plotly.restyle(el, { x: [[k == null ? null : cmp.map.x[k]]], y: [[k == null ? null : cmp.map.y[k]]] }, [el.data.length - 1]);
}

// Which mini-sector was clicked on a map (nearest point of the racing line)
export function sectorAt(cmp, x, y) {
  let best = 0, bestD = Infinity;
  cmp.map.x.forEach((mx, k) => { const d = (mx - x) ** 2 + (cmp.map.y[k] - y) ** 2; if (d < bestD) { bestD = d; best = k; } });
  const dist = cmp.map.distance[best];
  return cmp.sectors.find((s) => dist >= s.start_m && dist <= s.end_m) ?? null;
}

// Zoom a map to one mini-sector: a box around that stretch of line, with some margin
export function zoomMapTo(el, cmp, s) {
  if (!el?.data) return;
  const m = cmp.map, pad = (s.end_m - s.start_m) * 0.15, margin = 25;
  const ks = m.distance.map((d, k) => [d, k]).filter(([d]) => d >= s.start_m - pad && d <= s.end_m + pad).map(([, k]) => k);
  const xs = ks.map((k) => m.x[k]), ys = ks.map((k) => m.y[k]);
  Plotly.relayout(el, {
    "xaxis.range": [Math.min(...xs) - margin, Math.max(...xs) + margin],
    "yaxis.range": [Math.min(...ys) - margin, Math.max(...ys) + margin],
  });
}

export const resetMapZoom = (el) => el?.data && Plotly.relayout(el, { "xaxis.autorange": true, "yaxis.autorange": true });
