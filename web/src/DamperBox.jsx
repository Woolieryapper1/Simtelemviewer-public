import { useEffect, useState } from "react";
import { api } from "./api.js";
import { DAMPER_VIEWS, drawDampers } from "./charts.js";
import { num, signed } from "./format.js";
import Plot from "./Plot.jsx";
import { Checkbox, Input, Select, Segmented } from "./ui/index.js";

// Damper histograms box on the Metrics page: how much of the time each damper
// spends at each speed (+ bump, − rebound), for each compared lap or its whole
// run, optionally only in one corner gate. Its own options (what to count, the
// view, the speed bands) are remembered in this browser.

const GATE_OPTIONS = ["all", "Braking", "LS", "MS", "HS", "Power limited"];
const DEFAULTS = { scope: "lap", gate: "all", view: "corners", sharedY: true, diff: false, low: 25, high: 100 };
const STORAGE_KEY = "damperOptions";
// What + and − mean in each view (the table's footnote)
const SIGN_NOTE = {
  corners: "+ bump (compressing) · − rebound (extending)",
  axles: "both dampers of each axle together · + bump · − rebound",
  sides: "both dampers of each side together · + bump · − rebound",
  heave: "average of left and right: the axle moving up and down · + compressing · − extending",
  roll: "left minus right: the axle rolling · + left compressing faster · − right compressing faster",
};

function loadOptions() {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}") }; }
  catch { return { ...DEFAULTS }; }
}

// laps: the compared timed laps ({ sessionId, lap, slot, shortLabel, driver }); settings: the metric definitions (for gates)
export default function DamperBox({ laps, settings }) {
  const [opts, setOpts] = useState(loadOptions);
  const [bands, setBands] = useState({ low: opts.low, high: opts.high }); // typed values, applied once they make sense
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const change = (patch) => setOpts((prev) => {
    const next = { ...prev, ...patch };
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)); } catch { /* storage full or blocked: just don't remember */ }
    return next;
  });
  const setBand = (key, raw) => {
    const next = { ...bands, [key]: raw };
    setBands(next);
    const low = Number(next.low), high = Number(next.high);
    if (next.low !== "" && next.high !== "" && low > 0 && high > low) change({ low, high });
  };
  const bandProblem = !(Number(bands.low) > 0 && Number(bands.high) > Number(bands.low));

  const key = JSON.stringify([laps.map((l) => l.key), settings, opts.scope, opts.gate, opts.low, opts.high]);
  useEffect(() => {
    if (!laps.length) return;
    let stale = false;
    setError(null);
    api.dampers(laps, settings, { scope: opts.scope, gate: opts.gate, low_mm_s: opts.low, high_mm_s: opts.high })
      .then((d) => !stale && setResult({ key, d }))
      .catch((e) => !stale && setError(e.message));
    return () => { stale = true; };
  }, [key]);

  const d = result?.key === key ? result.d : null;
  // Name and colour each selection after its compared lap (or the first compared lap of its run)
  const sels = d ? d.selections.map((s, i) => {
    const lap = laps.find((l) => l.sessionId === s.session_id && (s.lap == null || l.lap === s.lap));
    const who = (lap?.driver ?? s.driver).slice(0, 12);
    return { slot: lap?.slot ?? i, label: s.lap == null ? `${who} run (${s.laps.length} laps)` : (lap?.shortLabel ?? `${who} L${s.lap}`) };
  }) : [];
  const multi = sels.length > 1;
  const diff = opts.diff && multi;
  const slots = sels.map((s) => s.slot).join();

  return (
    <div className="card chart-card">
      <div className="card-head">
        <h3>Damper histograms</h3>
        <span className="muted">% of time at each damper speed · + bump · − rebound</span>
      </div>
      <div className="toolbar pad damper-tools">
        <Segmented label="What to count" value={opts.scope} onChange={(scope) => change({ scope })}
          options={[{ value: "lap", label: "Each lap" }, { value: "run", label: "Whole run", title: "Every full timed lap" }]} />
        <label className="field">Only
          <Select size="sm" value={opts.gate} onChange={(e) => change({ gate: e.target.value })}>
            {GATE_OPTIONS.map((g) => <option key={g} value={g}>{g === "all" ? "the whole lap" : g}</option>)}
          </Select>
        </label>
        <Segmented label="Damper view" value={opts.view} onChange={(view) => change({ view })}
          options={Object.entries(DAMPER_VIEWS).map(([value, v]) => ({ value, label: v.label, title: SIGN_NOTE[value] }))} />
        <label className="field check"><Checkbox checked={opts.sharedY}
          onChange={(e) => change({ sharedY: e.target.checked })} />Same height scale</label>
        {multi && (
          <label className="field check" title="Each selection minus the reference (the first), bar by bar">
            <Checkbox checked={opts.diff} onChange={(e) => change({ diff: e.target.checked })} />Difference</label>
        )}
        <span className="field band-inputs" title="Speed bands, either way. Low speed: body roll and pitch from the driver. High speed: bumps and kerbs.">
          Low &lt; <Input size="sm" aria-label="Low damper speed threshold in mm/s" type="number" min="1" step="5" value={bands.low} onChange={(e) => setBand("low", e.target.value)} />
          High ≥ <Input size="sm" aria-label="High damper speed threshold in mm/s" type="number" min="1" step="5" value={bands.high} onChange={(e) => setBand("high", e.target.value)} /> mm/s
        </span>
        {bandProblem && <span className="bad-text">High must be above low</span>}
      </div>
      {error && <div className="error pad">{error}</div>}
      {!d && !error && <div className="box-empty">Loading…</div>}
      {d && (
        <>
          {d.note && <div className="note">{d.note}</div>}
          {d.selections.every((s) => s.time_s === 0)
            ? <div className="box-empty">None of these laps has any {opts.gate} running</div>
            : (
              <div className="damper-body">
                <Plot deps={[d, opts.view, opts.sharedY, diff, slots]}
                  draw={(el) => drawDampers(el, d, sels, { view: opts.view, sharedY: opts.sharedY, diff })} />
                <DamperTable d={d} sels={sels} view={opts.view} />
              </div>
            )}
        </>
      )}
    </div>
  );
}

// Band shares and bump / rebound numbers for each signal in the view, one column group per selection
function DamperTable({ d, sels, view }) {
  const signals = DAMPER_VIEWS[view].signals;
  const at = (sel, sig) => sel.histograms[d.signals.indexOf(sig)].stats;
  const low = d.settings.low_mm_s, high = d.settings.high_mm_s;
  // bands_pct order: rebound high, mid, low, bump low, mid, high
  const share = (st) => ({ low: st.bands_pct[2] + st.bands_pct[3], mid: st.bands_pct[1] + st.bands_pct[4], high: st.bands_pct[0] + st.bands_pct[5] });
  const split = (st, r, b) => `rebound ${num(st.bands_pct[r])}% · bump ${num(st.bands_pct[b])}%`;
  const delta = (v, ref, i) => (i && v != null && ref != null ? <span className="delta">{signed(v - ref, 1)}</span> : null);
  const ref = d.selections[0];
  return (
    <div className="table-scroll damper-table">
      <table className="data">
        <thead>
          <tr>
            <th />
            {d.selections.map((s, i) => (
              <th key={i} colSpan={5} className="group">
                <span className="lap-dot" style={{ background: `var(--lap-${sels[i].slot + 1})` }} /> {sels[i].label}
                <div className="muted small">{num(s.time_s, 1)} s · {s.estimated ? "estimated" : "logged"}</div>
              </th>
            ))}
          </tr>
          <tr>
            <th>Damper</th>
            {d.selections.flatMap((_, i) => [
              <th key={`l${i}`} className="num g" title={`Below ${low} mm/s either way`}>Low %</th>,
              <th key={`m${i}`} className="num" title={`${low}-${high} mm/s either way`}>Mid %</th>,
              <th key={`h${i}`} className="num" title={`${high} mm/s or more either way`}>High %</th>,
              <th key={`r${i}`} className="num" title="Time in bump ÷ time in rebound">Bump:reb</th>,
              <th key={`v${i}`} className="num" title="Average speed in bump / rebound, and the fastest (one sample)">Avg · peak mm/s</th>,
            ])}
          </tr>
        </thead>
        <tbody>
          {signals.map((sig) => (
            <tr key={sig}>
              <td>{sig}</td>
              {d.selections.flatMap((s, i) => {
                const st = at(s, sig), sh = share(st), rsh = share(at(ref, sig));
                return [
                  <td key={`l${i}`} className="num mono g" title={split(st, 2, 3)}>{num(sh.low)}{delta(sh.low, rsh.low, i)}</td>,
                  <td key={`m${i}`} className="num mono" title={split(st, 1, 4)}>{num(sh.mid)}{delta(sh.mid, rsh.mid, i)}</td>,
                  <td key={`h${i}`} className="num mono" title={split(st, 0, 5)}>{num(sh.high)}{delta(sh.high, rsh.high, i)}</td>,
                  <td key={`r${i}`} className="num mono">{num(st.bump_rebound_ratio, 2)}</td>,
                  <td key={`v${i}`} className="num mono">
                    <div>+{num(st.mean_bump_mm_s, 0)} · {num(st.peak_bump_mm_s, 0)}</div>
                    <div className="muted">{num(st.mean_rebound_mm_s, 0)} · {num(st.peak_rebound_mm_s, 0)}</div>
                  </td>,
                ];
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="hint table-foot">{SIGN_NOTE[view]}. Hover a % for the bump / rebound split.
        {sels.length > 1 && " Small numbers: difference from the reference (first)."}</p>
    </div>
  );
}
