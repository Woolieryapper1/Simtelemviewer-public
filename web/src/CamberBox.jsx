import { useEffect, useState } from "react";
import { api } from "./api.js";
import { drawCamber } from "./charts.js";
import { num, signed } from "./format.js";
import Plot from "./Plot.jsx";
import { Checkbox, Input, Segmented, Select } from "./ui/index.js";

// Camber optimiser box on the Metrics page (AC only): the camber each wheel really ran
// near the limit, scored against the tyre's camber curve from tyres.ini (or typed-in
// DCAMBER numbers), and the static camber change per axle that scores best.
// Its options are remembered in this browser.

const DEFAULTS = { scope: "run", minG: 0.6, compound: "", typed: false, f0: "", f1: "", r0: "", r1: "" };
const STORAGE_KEY = "camberOptions";

function loadOptions() {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}") }; }
  catch { return { ...DEFAULTS }; }
}

// laps: the compared timed laps ({ sessionId, lap, slot, shortLabel, driver })
export default function CamberBox({ laps }) {
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
  const request = {
    scope: opts.scope, min_g: opts.minG, compound: opts.compound,
    ...(front ? { front_dcamber0: front[0], front_dcamber1: front[1] } : {}),
    ...(rear ? { rear_dcamber0: rear[0], rear_dcamber1: rear[1] } : {}),
  };

  const key = JSON.stringify([laps.map((l) => l.key), request]);
  useEffect(() => {
    if (!laps.length) return;
    let stale = false;
    setError(null);
    api.camber(laps, request)
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
        <label className="field band-inputs" title="Only count moments using at least this share of the run's peak combined G">
          Near the limit ≥ <Input size="sm" aria-label="Share of peak G" type="number" min="0" max="1" step="0.05"
            value={opts.minG} onChange={(e) => e.target.value !== "" && change({ minG: Number(e.target.value) })} /> × peak G
        </label>
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
      {d && !error && (
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
                <div className="muted small">{num(s.time_s, 1)} s near the limit · {s.compound}</div>
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
