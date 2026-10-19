import { useState } from "react";
import { Button, Icon, Input, Select } from "./ui/index.js";

// Metric definitions: same names and defaults as MetricSettings in backend/app/metrics.py
export const DEFAULT_SETTINGS = {
  ls_max_kph: 80, ms_max_kph: 160, brake_pct: 5, power_throttle_pct: 98, power_lat_g: 0.5, power_rule: "or",
  balance_min_lat_g: 0.5, full_throttle_pct: 98, pedal_on_pct: 2,
};
const FS_PRESET = { ...DEFAULT_SETTINGS, ls_max_kph: 60, ms_max_kph: 90, power_rule: "and" };

// Remembered in this browser between visits
export function loadSettings() {
  try { return { ...DEFAULT_SETTINGS, ...JSON.parse(localStorage.getItem("metricSettings") ?? "{}") }; }
  catch { return { ...DEFAULT_SETTINGS }; }
}

// What's wrong with a set of definitions, or "" if they're fine
export function settingsProblem(s) {
  if (Object.values(s).some((v) => typeof v === "number" && !Number.isFinite(v))) return "Every definition needs a number";
  if (s.ms_max_kph <= s.ls_max_kph) return "The MS limit must be above the LS limit";
  return "";
}

// The definitions panel, shared by the Stint and Metrics pages. Edits are kept
// as a draft while typing and only applied once they make sense.
export default function Definitions({ settings, onChange, summary }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(settings);
  const problem = settingsProblem(draft);

  function set(key, raw) {
    const next = { ...draft, [key]: key === "power_rule" ? raw : raw === "" ? NaN : Number(raw) };
    setDraft(next);
    if (!settingsProblem(next)) onChange(next);
  }
  const apply = (values) => { setDraft(values); onChange(values); };
  const input = (key, step = 1) => (
    <Input size="sm" type="number" step={step} min="0" value={Number.isNaN(draft[key]) ? "" : draft[key]}
      aria-label={key.replaceAll("_", " ")} onChange={(e) => set(key, e.target.value)} />
  );
  const Gate = ({ g, children }) => <span className="gate-chip" style={{ "--c": `var(--gate-${g})` }}>{children}</span>;

  return (
    <div className="card defs">
      <button type="button" className="defs-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="defs-title"><Icon name={open ? "chevron-down" : "chevron-right"} /> Metric definitions</span>
        <span className="muted">{summary}</span>
      </button>
      {open && (
        <div className="defs-body">
          <fieldset>
            <legend>Corner gates <span className="muted">first match wins</span></legend>
            <div className="def-row"><Gate g={0}>Braking</Gate> brake &gt; {input("brake_pct")} %</div>
            <div className="def-row"><Gate g={4}>Power limited</Gate> throttle ≥ {input("power_throttle_pct")} %
              <Select size="sm" value={draft.power_rule} aria-label="Power limited rule" onChange={(e) => set("power_rule", e.target.value)}>
                <option value="or">or</option><option value="and">and</option>
              </Select>
              |lat G| &lt; {input("power_lat_g", 0.1)} g</div>
            <div className="def-row"><Gate g={1}>LS</Gate> cornering below {input("ls_max_kph", 5)} kph</div>
            <div className="def-row"><Gate g={2}>MS</Gate> cornering below {input("ms_max_kph", 5)} kph</div>
            <div className="def-row"><Gate g={3}>HS</Gate> cornering above that</div>
          </fieldset>
          <fieldset>
            <legend>Balance and driver inputs</legend>
            <div className="def-row">Balance measured when |lat G| ≥ {input("balance_min_lat_g", 0.1)} g</div>
            <div className="def-row">Full throttle = throttle ≥ {input("full_throttle_pct")} %</div>
            <div className="def-row">Pedal counts as pressed above {input("pedal_on_pct")} %</div>
            <div className="def-row">
              <Button icon="reset" onClick={() => apply(DEFAULT_SETTINGS)}>Reset to defaults</Button>
              <Button title="LS < 60 kph, MS < 90 kph, power limited = flat out and nearly straight"
                onClick={() => apply(FS_PRESET)}>FS track preset</Button>
            </div>
          </fieldset>
          {problem && <div className="error">{problem}</div>}
        </div>
      )}
    </div>
  );
}
