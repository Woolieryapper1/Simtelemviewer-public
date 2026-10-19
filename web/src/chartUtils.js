// Shared bits for the Plotly charts.

export const Plotly = window.Plotly; // loaded by the <script> tag in index.html

// Read a colour from theme.css, so charts use the same palette as the page
export const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
export const lapColor = (slot) => css(`--lap-${slot + 1}`);

export const PLOT_CONFIG = { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["select2d", "lasso2d"] };

// Line style per compared lap (reference first), used when a row overlays several channels
export const LAP_DASHES = ["solid", "dash", "dot", "dashdot", "longdash", "longdashdot"];

// Index of the value in a sorted array closest to x
export function nearestIndex(xs, x) {
  let lo = 0, hi = xs.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (xs[mid] <= x) lo = mid; else hi = mid;
  }
  return x - xs[lo] < xs[hi] - x ? lo : hi;
}

// Linear interpolation (like numpy.interp): value of y at position x
function interp(x, xs, ys) {
  if (x <= xs[0]) return ys[0];
  if (x >= xs[xs.length - 1]) return ys[ys.length - 1];
  const i = nearestIndex(xs, x), lo = xs[i] <= x ? i : i - 1, hi = lo + 1;
  return ys[lo] + ((x - xs[lo]) / (xs[hi] - xs[lo])) * (ys[hi] - ys[lo]);
}

// Time gained/lost vs the reference at every metre: positive = slower than the reference
export function timeDelta(data, refData) {
  return data.Distance.map((x, i) => data.Time[i] - interp(x, refData.Distance, refData.Time));
}

// "Tire Temp Core FL" + "Tire Temp Core RR" -> "Tire Temp Core"
export function rowTitle(names, units) {
  const unitList = [...new Set(names.map((n) => units[n]).filter(Boolean))];
  const unitText = unitList.length ? `  ${unitList.join(", ")}` : "";
  if (names.length === 1) return names[0] + unitText;
  const words = names.map((n) => n.split(" "));
  const common = [];
  for (let i = 0; words.every((w) => i < w.length - 1 && w[i] === words[0][i]); i++) common.push(words[0][i]);
  return (common.length ? common.join(" ") : names.join(" / ")) + unitText;
}
