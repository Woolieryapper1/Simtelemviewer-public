import { useEffect, useState } from "react";
import { Button, Checkbox, Input, Popover, Select } from "./ui/index.js";

// Which laps the Stint page counts: same names and defaults as LapFilter in
// backend/fs_telemetry/stint.py. Out and unfinished laps are left out by default,
// and so are laps far slower than the best one (a cool-down or a spin).
export const DEFAULT_LAP_FILTER = { include_partial: false, slow_filter: "percent", slow_pct: 10 };
const SLOW_FILTERS = ["percent", "first_last", "off"];
const STORAGE_KEY = "stintLapFilter";
const goodPct = (v) => typeof v === "number" && Number.isFinite(v) && v > 0;

// Remembered in this browser between visits; anything odd falls back to the default
export function loadLapFilter() {
  try {
    const f = { ...DEFAULT_LAP_FILTER, ...JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}") };
    return {
      include_partial: f.include_partial === true,
      slow_filter: SLOW_FILTERS.includes(f.slow_filter) ? f.slow_filter : DEFAULT_LAP_FILTER.slow_filter,
      slow_pct: goodPct(f.slow_pct) ? f.slow_pct : DEFAULT_LAP_FILTER.slow_pct,
    };
  } catch { return { ...DEFAULT_LAP_FILTER }; }
}

export function saveLapFilter(filter) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(filter)); } catch { /* storage off: just not remembered */ }
}

// One line for the button: what's being left out right now
function describe(f) {
  const slow = f.slow_filter === "percent" ? `within ${f.slow_pct}% of best`
    : f.slow_filter === "first_last" ? "no first or last lap" : "every timed lap";
  return f.include_partial ? `${slow} + out laps` : slow;
}

// "Laps counted" button with a small popover to choose which laps count towards
// the stint numbers. Closes on a click outside or Escape.
export default function LapFilterMenu({ filter, onChange, summary }) {
  const [pctDraft, setPctDraft] = useState(String(filter.slow_pct)); // kept as typed until it's a usable number

  useEffect(() => setPctDraft(String(filter.slow_pct)), [filter.slow_pct]);

  const set = (key, value) => onChange({ ...filter, [key]: value });
  function setPct(raw) {
    setPctDraft(raw);
    const v = Number(raw);
    if (raw.trim() !== "" && goodPct(v) && v !== filter.slow_pct) set("slow_pct", v); // ignore blanks, 0 and below
  }
  const pctBad = !goodPct(Number(pctDraft)) || pctDraft.trim() === "";
  const isDefault = Object.keys(DEFAULT_LAP_FILTER).every((k) => filter[k] === DEFAULT_LAP_FILTER[k]);

  return (
    <Popover align="right" width={360} label="Laps counted" className="lap-filter"
      trigger={<Button icon="sliders">Laps counted <span className="muted">{describe(filter)}</span></Button>}>
      <label className="def-row" title="Out and unfinished laps only ever count towards the fuel, tyre and input averages and the charts, never towards lap times">
        <Checkbox autoFocus checked={filter.include_partial} onChange={(e) => set("include_partial", e.target.checked)} />
        Include out and unfinished laps
      </label>
      <label className="def-row">
        Grey out slow laps
        <Select className="block" value={filter.slow_filter} onChange={(e) => set("slow_filter", e.target.value)}>
          <option value="percent">More than {filter.slow_pct}% slower than the best</option>
          <option value="first_last">The first and last timed lap</option>
          <option value="off">None: keep every lap</option>
        </Select>
      </label>
      <label className="def-row" title="Compared with the best valid lap of the run">
        More than
        <Input size="sm" type="number" min="0.5" step="1" value={pctDraft} disabled={filter.slow_filter !== "percent"}
          aria-invalid={pctBad} onChange={(e) => setPct(e.target.value)} />
        % slower than the best
      </label>
      {pctBad && filter.slow_filter === "percent" && <div className="error">Needs a number above 0 (still using {filter.slow_pct}%)</div>}
      {summary && (
        <p className="hint">
          Lap times use {summary.representative_laps} {summary.representative_laps === 1 ? "lap" : "laps"};
          fuel and tyre averages use {summary.averaged_laps}. Invalid laps still count towards fuel and tyres. Saved Use / Ignore flags override these automatic filters.
        </p>
      )}
      <div className="def-row">
        <Button size="sm" icon="reset" disabled={isDefault} onClick={() => onChange({ ...DEFAULT_LAP_FILTER })}>
          Reset to default
        </Button>
      </div>
    </Popover>
  );
}
