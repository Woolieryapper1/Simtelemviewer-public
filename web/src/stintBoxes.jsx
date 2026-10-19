import { fmtLap, fmtSplit, num } from "./format.js";

// Everything the Stint page can show as a summary tile or a trend chart.
// The page shows a list of these by id; the user can add, remove and reorder
// them, and the list is remembered per car (a Supercar has no KERS, the
// hybrid MFT02 does).

const CORNERS = ["FL", "FR", "RL", "RR"];
const rep = (laps) => laps.filter((l) => l.in_pace); // full valid laps the lap filter counts (as the backend's lap times)
const mean = (vals) => {
  const v = vals.filter((x) => x != null);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
};
// Does any lap have a real (non-zero) value? Used to hide things a car doesn't have.
const anyValue = (values) => values.some((v) => v != null && Number.isFinite(v) && v !== 0);
const tyreValues = (laps, key) => laps.flatMap((l) => CORNERS.map((c) => l[key]?.[c]));
const worst = (obj) => Object.entries(obj ?? {}).sort((a, b) => b[1] - a[1])[0] ?? [null, null];
const perTyre = (obj, d) => CORNERS.filter((c) => c in (obj ?? {})).map((c) => `${c} ${num(obj[c], d)}`).join(" · ");

// ---------- tiles ----------
// value(stint) -> { value, sub }; has(stint) -> false hides it by default
export const TILES = {
  best: { label: "Best lap", has: () => true,
    value: ({ summary: sm }) => ({ value: fmtLap(sm.best_lap_s), sub: `of ${sm.representative_laps} counted valid ${sm.representative_laps === 1 ? "lap" : "laps"}` }) },
  theoretical: { label: "Theoretical best", has: ({ summary: sm }) => sm.theoretical_best_s != null,
    value: ({ summary: sm }) => {
      const gain = sm.theoretical_best_s != null && sm.best_lap_s != null ? sm.theoretical_best_s - sm.best_lap_s : null;
      const tail = gain == null ? "" : Math.abs(gain) < 0.005 ? " · = best lap" : ` · ${gain.toFixed(3)} s`;
      return { value: fmtLap(sm.theoretical_best_s), sub: sm.best_splits_s.length ? sm.best_splits_s.map(fmtSplit).join(" + ") + tail : "" };
    } },
  average: { label: "Average lap", has: () => true,
    value: ({ summary: sm }) => ({ value: fmtLap(sm.average_lap_s), sub: sm.consistency_s == null ? "" : `consistency σ ${num(sm.consistency_s, 3)} s` }) },
  fuel: { label: "Fuel per lap", has: ({ summary: sm }) => sm.fuel_per_lap != null && sm.fuel_per_lap > 0,
    value: ({ summary: sm }) => ({ value: sm.fuel_per_lap == null ? "–" : `${num(sm.fuel_per_lap, 3)} L`,
      sub: sm.fuel_left == null ? "" : `${num(sm.fuel_left, 2)} L left ≈ ${num(sm.laps_of_fuel_left, 1)} laps` }) },
  grip: { label: "Grip lost per lap", has: ({ summary: sm }) => anyValue(Object.values(sm.grip_lost_per_lap ?? {})),
    value: ({ summary: sm }) => {
      const [c, v] = worst(sm.grip_lost_per_lap);
      return { value: v == null ? "–" : `${c} ${num(v, 2)} %`, sub: perTyre(sm.grip_lost_per_lap, 2) };
    } },
  vkm: { label: "Tyre vKm per lap", has: ({ summary: sm }) => anyValue(Object.values(sm.vkm_per_lap ?? {})),
    value: ({ summary: sm }) => {
      const [c, v] = worst(sm.vkm_per_lap);
      return { value: v == null ? "–" : `${c} ${num(v, 3)}`, sub: perTyre(sm.vkm_per_lap, 3) };
    } },
  kers: { label: "KERS per lap", has: ({ laps }) => anyValue(laps.map((l) => l.kers_deployed_kj)),
    value: ({ summary: sm }) => ({ value: sm.kers_per_lap_kj == null ? "–" : `${num(sm.kers_per_lap_kj, 1)} kJ` }) },
  gAbs: { label: "G absolute", has: ({ summary: sm }) => sm.g_abs_avg != null,
    value: ({ summary: sm }) => ({ value: `${num(sm.g_abs_avg, 2)} G`, sub: `lap average · ${num(sm.g_abs_p95, 2)} G 95th percentile` }) },
  topSpeed: { label: "Top speed", has: ({ laps }) => anyValue(laps.map((l) => l.top_speed_kph)),
    value: ({ laps }) => {
      const v = Math.max(...laps.map((l) => l.top_speed_kph ?? 0));
      return { value: `${num(v, 1)} kph`, sub: "fastest point of the run" };
    } },
  fullThrottle: { label: "Full throttle", has: ({ laps }) => anyValue(laps.map((l) => l.full_throttle_pct)),
    value: ({ laps }) => ({ value: `${num(mean(rep(laps).map((l) => l.full_throttle_pct)), 1)} %`, sub: "of the lap, counted valid laps" }) },
  braking: { label: "Braking", has: ({ laps }) => anyValue(laps.map((l) => l.braking_pct)),
    value: ({ laps }) => ({ value: `${num(mean(rep(laps).map((l) => l.braking_pct)), 1)} %`, sub: "of the lap, counted valid laps" }) },
  coasting: { label: "Coasting", has: ({ laps }) => anyValue(laps.map((l) => l.coasting_pct)),
    value: ({ laps }) => ({ value: `${num(mean(rep(laps).map((l) => l.coasting_pct)), 1)} %`, sub: "of the lap, counted valid laps" }) },
  overlap: { label: "Throttle/brake overlap", has: ({ laps }) => anyValue(laps.map((l) => l.overlap_pct)),
    value: ({ laps }) => ({ value: `${num(mean(rep(laps).map((l) => l.overlap_pct)), 1)} %`, sub: "of the lap, counted valid laps" }) },
};

// ---------- trend charts ----------
// series(laps) -> [{ name, slot, values }]. Tyres always use slots FL=0 FR=1 RL=2 RR=3.
const one = (name, key) => (laps) => [{ name, slot: 0, values: laps.map((l) => l[key] ?? null) }];
const tyres = (key) => (laps) => CORNERS.map((c, i) => ({ name: c, slot: i, values: laps.map((l) => l[key]?.[c] ?? null) }));
const hasOne = (key) => ({ laps }) => anyValue(laps.map((l) => l[key]));
const hasTyres = (key) => ({ laps }) => anyValue(tyreValues(laps, key));

export const CHARTS = {
  laptime: { title: "Lap time", unit: "s", series: one("Lap time", "lap_time_s"), has: () => true },
  sectors: { title: "Sector times", unit: "s", has: ({ laps }) => laps.some((l) => l.splits_s?.length),
    series: (laps) => {
      const n = Math.max(0, ...laps.map((l) => l.splits_s?.length ?? 0));
      return Array.from({ length: n }, (_, k) => ({ name: `S${k + 1}`, slot: k, values: laps.map((l) => l.splits_s?.[k] ?? null) }));
    } },
  fuel: { title: "Fuel used", unit: "L", series: one("Fuel", "fuel_used"), bars: true, has: hasOne("fuel_used") },
  fuelLeft: { title: "Fuel left at end of lap", unit: "L", series: one("Fuel left", "fuel_left"), has: hasOne("fuel_left") },
  grip: { title: "Tyre grip at end of lap", unit: "%", series: tyres("grip_end"), has: hasTyres("grip_end") },
  gripLost: { title: "Tyre grip lost per lap", unit: "%", series: tyres("grip_lost"), has: hasTyres("grip_lost") },
  vkm: { title: "Tyre vKm per lap", unit: "vkm", series: tyres("vkm"), has: hasTyres("vkm") },
  temp: { title: "Tyre core temp, lap average", unit: "°C", series: tyres("core_temp_avg"), has: hasTyres("core_temp_avg") },
  pressure: { title: "Tyre pressure, lap average", unit: "psi", series: tyres("pressure_avg"), has: hasTyres("pressure_avg") },
  kers: { title: "KERS deployed", unit: "kJ", series: one("KERS", "kers_deployed_kj"), bars: true, has: hasOne("kers_deployed_kj") },
  kersCharge: { title: "KERS charge at end of lap", unit: "%", series: one("KERS charge", "kers_charge_end"), has: hasOne("kers_charge_end") },
  gAbs: { title: "G absolute", unit: "G", has: hasOne("g_abs_avg"),
    series: (laps) => [{ name: "Average", slot: 0, values: laps.map((l) => l.g_abs_avg ?? null) },
      { name: "95th percentile", slot: 1, values: laps.map((l) => l.g_abs_p95 ?? null) }] },
  topSpeed: { title: "Top speed", unit: "kph", series: one("Top speed", "top_speed_kph"), has: hasOne("top_speed_kph") },
  fullThrottle: { title: "Full throttle", unit: "% of lap", series: one("Full throttle", "full_throttle_pct"), has: hasOne("full_throttle_pct") },
  braking: { title: "Braking", unit: "% of lap", series: one("Braking", "braking_pct"), has: hasOne("braking_pct") },
  coasting: { title: "Coasting", unit: "% of lap", series: one("Coasting", "coasting_pct"), has: hasOne("coasting_pct") },
  overlap: { title: "Throttle/brake overlap", unit: "% of lap", series: one("Overlap", "overlap_pct"), has: hasOne("overlap_pct") },
};

const DEFAULT = {
  tiles: ["best", "theoretical", "average", "fuel", "grip", "vkm", "kers", "gAbs"],
  charts: ["laptime", "fuel", "grip", "temp", "pressure", "kers"],
};

// ---------- the user's layout, remembered per car in this browser ----------
const storageKey = (car) => `stintLayout:${car}`;

// Defaults leave out anything this run has no data for (e.g. KERS on a car without it)
export function defaultLayout(stint) {
  return {
    tiles: DEFAULT.tiles.filter((id) => TILES[id].has(stint)),
    charts: DEFAULT.charts.filter((id) => CHARTS[id].has(stint)),
  };
}

export function loadLayout(stint) {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey(stint.car)) ?? "null");
    if (saved) {
      return { // drop anything that no longer exists
        tiles: (saved.tiles ?? []).filter((id) => id in TILES),
        charts: (saved.charts ?? []).filter((id) => id in CHARTS),
        custom: true,
      };
    }
  } catch { /* corrupt entry: fall back to defaults */ }
  return { ...defaultLayout(stint), custom: false };
}

export function saveLayout(car, layout) {
  localStorage.setItem(storageKey(car), JSON.stringify({ tiles: layout.tiles, charts: layout.charts }));
}

export function resetLayout(car) {
  localStorage.removeItem(storageKey(car));
}
