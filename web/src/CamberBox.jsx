import { useEffect, useState } from "react";
import { api } from "./api.js";
import { drawCamber } from "./charts.js";
import { num, signed } from "./format.js";
import Plot from "./Plot.jsx";
import { Checkbox, Input, Segmented, Select } from "./ui/index.js";

// Camber optimiser box on the Metrics page (AC only): the camber each wheel really ran
// near the limit, scored against the tyre's camber curve from tyres.ini (or typed-in
// DCAMBER numbers), and the static camber change per axle that scores best.
// What counts as "near the limit" can be narrowed to one corner gate or a band of
// G, e.g. to look at corner entry rather than the apex. Its options are
// remembered in this browser.

const GATE_OPTIONS = ["all", "Braking", "LS", "MS", "HS", "Power limited"];
const DEFAULTS = { scope: "run", gate: "all", peakPct: 98, minG: 0.6, maxG: "", compound: "", typed: false, f0: "", f1: "", r0: "", r1: "" };
const STORAGE_KEY = "camberOptions";

function loadOptions() {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}") }; }
  catch { return { ...DEFAULTS }; }
}

// laps: the compared timed laps ({ sessionId, lap, slot, shortLabel, driver }); settings: the metric definitions (for gates)
export default function CamberBox({ laps, settings }) {
  const [opts, setOpts] = useState(loadOptions);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const change = (patch) => setOpts((prev) => {
    const next = { ...prev, ...patch };
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)); } catch { /* storage full or blocked: just don't remember */ }
    return next;
  });
  // Typed tyre numbers are only sent once a whole axle pair is filled in, DCAMBER_1 negative
  const pair = (a, b) => (a !== "" && b !== "" && Number(b) < 0 ? [Number(a), Number(b)] : null);
  const front = opts.typed ? pair(opts.f0, opts.f1) : null, rear = opts.typed ? pair(opts.r0, opts.r1) : null;
  // The upper G limit only counts once it's above the lower one
  const maxG = opts.maxG !== "" && Number(opts.maxG) > opts.minG ? Number(opts.maxG) : "";
  const peakOk = opts.peakPct !== "" && Number(opts.peakPct) >= 50 && Number(opts.peakPct) <= 100;
  const peakPct = peakOk ? Number(opts.peakPct) : DEFAULTS.peakPct;
  const request = {
    scope: opts.scope, gate: opts.gate, peak_pct: peakPct, min_g: opts.minG, max_g: maxG, compound: opts.compound,
    ...(front ? { front_dcamber0: front[0], front_dcamber1: front[1] } : {}),
    ...(rear ? { rear_dcamber0: rear[0], rear_dcamber1: rear[1] } : {}),
  };

  const key = JSON.stringify([laps.map((l) => l.key), settings, request]);
  useEffect(() => {
    if (!laps.length) return;
    let stale = false;
    setError(null);
    api.camber(laps, settings, request)
      .then((d) => !stale && setResult({ key, d }))
      .catch((e) => !stale && setError(e.message));
    return () => { stale = true; };
  }, [key]);

  const d = result?.key === key ? result.d : null;
  const sels = d ? d.selections.map((s, i) => {
    const lap = laps.find((l) => l.sessionId === s.session_id && (s.lap == null || l.lap === s.lap));
    const who = (lap?.driver ?? s.driver).slice(0, 12);
    return { slot: lap?.slot ?? i, label: s.lap == null ? `${who} run (${s.laps.length} laps)` : (lap?.shortLabel ?? `${who} L${s.lap}`) };
  }) : [];
  const compounds = d?.selections[0]?.compounds ?? [];
  const tyreInput = (k, label) => (
    <Input size="sm" aria-label={label} type="number" step="0.01" value={opts[k]} onChange={(e) => change({ [k]: e.target.value })} />
  );

  return (
    <div className="card chart-card">
      <div className="card-head">
        <h3>Camber optimiser <span className="tag warn" title={d?.note}>est.</span></h3>
        <span className="muted">camber the wheels ran near the limit, scored on the tyre's camber curve · AC only</span>
      </div>
      <div className="toolbar pad damper-tools">
        <Segmented label="What to count" value={opts.scope} onChange={(scope) => change({ scope })}
          options={[{ value: "lap", label: "Each lap" }, { value: "run", label: "Whole run", title: "Every full timed lap" }]} />
        <label className="field">Only
          <Select size="sm" value={opts.gate} onChange={(e) => change({ gate: e.target.value })}>
            {GATE_OPTIONS.map((g) => <option key={g} value={g}>{g === "all" ? "the whole lap" : g}</option>)}
          </Select>
        </label>
        <span className="field band-inputs" title="Only count moments whose combined G is in this band. Peak G is this percentile of combined G over the whole laps (below 100 ignores kerb spikes). Leave the upper limit empty for no limit; e.g. 0.4 to 0.8 looks at corner entry and exit rather than the apex.">
          <Input size="sm" aria-label="Lower share of peak G" type="number" min="0" max="1" step="0.05"
            value={opts.minG} onChange={(e) => e.target.value !== "" && change({ minG: Number(e.target.value) })} /> to
          <Input size="sm" aria-label="Upper share of peak G (empty: no limit)" type="number" min="0" max="2" step="0.05" placeholder="max"
            value={opts.maxG} onChange={(e) => change({ maxG: e.target.value })} /> × peak G · peak =
          <Input size="sm" aria-label="Percentile of combined G that counts as peak" type="number" min="50" max="100" step="1"
            value={opts.peakPct} onChange={(e) => change({ peakPct: e.target.value })} />th percentile
        </span>
        {opts.maxG !== "" && maxG === "" && <span className="bad-text">Upper limit must be above the lower</span>}
        {!peakOk && <span className="bad-text">Peak percentile: 50 to 100 (using {DEFAULTS.peakPct})</span>}
        {compounds.length > 1 && (
          <label className="field">Compound
            <Select size="sm" value={opts.compound} onChange={(e) => change({ compound: e.target.value })}>
              <option value="">car default</option>
              {compounds.map((c, i) => <option key={i} value={i}>{c}</option>)}
            </Select>
          </label>
        )}
        <label className="field check" title="Use your own DCAMBER_0 / DCAMBER_1 instead of the car's tyres.ini">
          <Checkbox checked={opts.typed} onChange={(e) => change({ typed: e.target.checked })} />Type tyre numbers</label>
        {opts.typed && (
          <span className="field band-inputs" title="DCAMBER_0 and DCAMBER_1 (negative) from tyres.ini, per axle">
            Front {tyreInput("f0", "Front DCAMBER_0")} {tyreInput("f1", "Front DCAMBER_1")}
            Rear {tyreInput("r0", "Rear DCAMBER_0")} {tyreInput("r1", "Rear DCAMBER_1")}
          </span>
        )}
      </div>
      {error && <div className="error pad">{error}</div>}
      {!d && !error && <div className="box-empty">Loading…</div>}
      {d && !error && d.selections.every((s) => s.time_s === 0) && (
        <div className="box-empty">Nothing to count: no {opts.gate === "all" ? "" : `${opts.gate} `}running in that G band</div>
      )}
      {d && !error && !d.selections.every((s) => s.time_s === 0) && (
        <>
          <div className="note">{d.note}</div>
          <div className="damper-body">
            <Plot deps={[d, sels.map((s) => s.slot).join()]} draw={(el) => drawCamber(el, d, sels)} />
            <CamberTable d={d} sels={sels} />
          </div>
        </>
      )}
    </div>
  );
}

// Per selection: the tyre peak, the camber each wheel ran, and the best change per axle
function CamberTable({ d, sels }) {
  return (
    <div className="table-scroll damper-table">
      <table className="data">
        <thead>
          <tr>
            <th />
            {d.selections.map((s, i) => (
              <th key={i} colSpan={3} className="group">
                <span className="lap-dot" style={{ background: `var(--lap-${sels[i].slot + 1})` }} /> {sels[i].label}
                <div className="muted small">{num(s.time_s, 1)} s counted · peak {num(s.peak_g, 2)} g · {s.compound}</div>
              </th>
            ))}
          </tr>
          <tr>
            <th>Wheel</th>
            {d.selections.flatMap((_, i) => [
              <th key={`m${i}`} className="num g" title="Load-weighted average camber near the limit">Ran °</th>,
              <th key={`r${i}`} className="num" title="10th to 90th percentile">Range °</th>,
              <th key={`p${i}`} className="num" title="The tyre's peak-grip camber">Peak °</th>,
            ])}
          </tr>
        </thead>
        <tbody>
          {["FL", "FR", "RL", "RR"].map((c) => (
            <tr key={c}>
              <td>{c}</td>
              {d.selections.flatMap((s, i) => {
                const w = s.corners.find((x) => x.corner === c), t = s.tyres.find((x) => x.axle === (c[0] === "F" ? "Front" : "Rear"));
                return [
                  <td key={`m${i}`} className="num mono g">{num(w.mean_deg, 2)}</td>,
                  <td key={`r${i}`} className="num mono">{num(w.p10_deg, 1)} to {num(w.p90_deg, 1)}</td>,
                  <td key={`p${i}`} className="num mono" title={`${t.source}: DCAMBER_0 ${t.dcamber0}, DCAMBER_1 ${t.dcamber1}`}>{num(t.peak_deg, 2)}</td>,
                ];
              })}
            </tr>
          ))}
          {["Front", "Rear"].map((axle) => (
            <tr key={axle}>
              <td><b>{axle}</b></td>
              {d.selections.flatMap((s, i) => {
                const a = s.axles.find((x) => x.axle === axle);
                return [
                  <td key={`b${i}`} colSpan={3} className="num mono g">
                    {a.best_offset_deg == null ? "–" : <>
                      <b>{signed(a.best_offset_deg, 1)}°</b> static
                      <span className="muted"> · score {num(a.current_pct, 2)}% → {num(a.best_pct, 2)}%</span>
                      {a.at_limit && <span className="bad-text"> · at the end of the range</span>}
                    </>}
                  </td>,
                ];
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="hint table-foot">Change to static camber per axle: − = more negative. Score 100% = every loaded moment at the tyre's peak.</p>
    </div>
  );
}
