// Smoothing and cross-channel scaling for a telemetry row, applied in the
// browser to what's drawn and shown in the readout (the run itself is never changed).
// Smoothing runs first, then scaling. Laps are on an evenly spaced distance grid
// (useLapData asks for one sample every 2 m), so a window in metres is a fixed
// number of samples.

// Smoothing presets: a centred moving average this many metres wide (0 = off)
export const SMOOTHING = [0, 10, 30, 60];

// How a row's channels are put on a scale. Only rows with several channels offer
// anything but "same".
//   same:  every channel on one scale, as logged
//   fit:   each channel stretched to fill the row (0 = its lowest over the compared laps, 1 = its highest)
//   share: each channel as a % of the row's channels added up at that point (e.g. one tyre's share of the load)
//   mean:  each channel divided by its own average over the lap (1 = average)
//   diff:  each channel minus the row's first channel (e.g. FL - FR)
export const SCALES = {
  same: { label: "Same scale", hint: "Every channel on one scale, as logged" },
  fit: { label: "Each 0-1", hint: "Each channel stretched to fill the row, so small ones show next to big ones. The readout still shows real values" },
  share: { label: "% of row total", hint: "Each channel as a share of the row's channels added up, e.g. one tyre's share of the total load. Only meaningful for channels that are never negative" },
  mean: { label: "÷ lap mean", hint: "Each channel divided by its own average over the lap: 1 = average, 1.2 = 20% above it" },
  diff: { label: "Minus first", hint: "Every channel minus the row's first channel, e.g. FL - FR" },
};

// The unit of a channel once its row is scaled ("%" for share, "× mean" for mean, else as logged)
export const scaledUnit = (scale, logged) => (scale === "share" ? "%" : scale === "mean" ? "× mean" : logged ?? "");

// Centred moving average over `samples` points; gaps (null/NaN) are skipped,
// and the window shrinks at the ends of the lap
export function smooth(values, samples) {
  if (samples <= 1) return values;
  const half = Math.floor(samples / 2), n = values.length;
  const sum = new Float64Array(n + 1), count = new Uint32Array(n + 1);
  for (let i = 0; i < n; i++) {
    const v = values[i], ok = v != null && Number.isFinite(v);
    sum[i + 1] = sum[i] + (ok ? v : 0);
    count[i + 1] = count[i] + (ok ? 1 : 0);
  }
  return values.map((v, i) => {
    if (v == null || !Number.isFinite(v)) return v;
    const lo = Math.max(0, i - half), hi = Math.min(n, i + half + 1);
    return (sum[hi] - sum[lo]) / (count[hi] - count[lo]);
  });
}

const finite = (v) => v != null && Number.isFinite(v);
const mean = (values) => {
  let s = 0, k = 0;
  for (const v of values) if (finite(v)) { s += v; k++; }
  return k ? s / k : null;
};

// Lowest and highest value over all the laps' series (so every lap shares one scale)
function range(series) {
  let lo = Infinity, hi = -Infinity;
  for (const values of series) for (const v of values ?? []) if (finite(v)) { if (v < lo) lo = v; if (v > hi) hi = v; }
  return lo <= hi ? [lo, hi] : null;
}

// One row's channels for every compared lap, smoothed and scaled.
//   channels: the row's channels; laps: compared laps with .data
//   opts: { scale, smooth } (smooth in metres)
// Returns { values, plot, ranges, unit }:
//   values[lap][channel] - what the readout shows
//   plot[lap][channel]   - what's drawn (the same, except "fit" stretches it to 0-1)
//   ranges[channel]      - lowest and highest value, for "fit"
//   unit(channel, logged unit) - the unit of the shown values
export function rowSeries(channels, laps, opts = {}) {
  const scale = channels.length > 1 && SCALES[opts.scale] ? opts.scale : "same";
  const values = laps.map((lap) => {
    const d = lap.data, step = d.Distance?.length > 1 ? d.Distance[1] - d.Distance[0] : 2;
    const samples = opts.smooth ? Math.max(1, Math.round(opts.smooth / step)) : 1;
    const raw = Object.fromEntries(channels.map((ch) => [ch, d[ch] ? smooth(d[ch], samples) : null]));
    if (scale === "share") {
      const total = (d.Distance ?? []).map((_, i) => channels.reduce((s, ch) => s + (finite(raw[ch]?.[i]) ? raw[ch][i] : 0), 0));
      return Object.fromEntries(channels.map((ch) => [ch, raw[ch]?.map((v, i) => (finite(v) && Math.abs(total[i]) > 1e-9 ? (100 * v) / total[i] : null))]));
    }
    if (scale === "mean") {
      return Object.fromEntries(channels.map((ch) => {
        const m = raw[ch] && mean(raw[ch]);
        return [ch, raw[ch]?.map((v) => (finite(v) && m && Math.abs(m) > 1e-9 ? v / m : null))];
      }));
    }
    if (scale === "diff") {
      const first = raw[channels[0]];
      return Object.fromEntries(channels.map((ch) => [ch, raw[ch]?.map((v, i) => (finite(v) && finite(first?.[i]) ? v - first[i] : null))]));
    }
    return raw;
  });
  const ranges = scale === "fit" ? Object.fromEntries(channels.map((ch) => [ch, range(values.map((v) => v[ch]))])) : {};
  const plot = scale !== "fit" ? values : values.map((v) => Object.fromEntries(channels.map((ch) => {
    const r = ranges[ch];
    return [ch, r && v[ch]?.map((x) => (finite(x) ? (x - r[0]) / (r[1] - r[0] || 1) : null))];
  })));
  const unit = (ch, logged) => scaledUnit(scale, logged);
  return { scale, values, plot, ranges, unit };
}

// A few words for the row header on what's been done to it
export function describe(opts, channels) {
  const scale = channels.length > 1 ? opts.scale : null;
  const parts = [];
  if (scale === "fit") parts.push("each channel scaled to fit");
  if (scale === "share") parts.push("% of row total");
  if (scale === "mean") parts.push("÷ lap mean");
  if (scale === "diff") parts.push(`minus ${channels[0]}`);
  if (opts.smooth) parts.push(`smoothed ${opts.smooth} m`);
  return parts.join(" · ");
}
