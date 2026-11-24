import { useEffect, useState } from "react";
import { api } from "./api.js";
import { drawPressure } from "./charts.js";
import { num, signed } from "./format.js";
import Plot from "./Plot.jsx";
import { Input, Segmented, Select } from "./ui/index.js";

// Tyre pressure box on the Metrics page (AC only): each tyre's hot pressure and tread temperature near the
// limit, scored against the car's PRESSURE_IDEAL / PRESSURE_D_GAIN and temperature curve from tyres.ini, and
// the cold pressure change that lands the hot pressure on the ideal, now and for a hotter or cooler tread.
// What counts as "near the limit" is chosen as in the camber box. Its options are remembered in this browser.

const GATE_OPTIONS = ["all", "Braking", "LS", "MS", "HS", "Power limited"];
const DEFAULTS = { scope: "run", gate: "all", gBasis: "auto", minGain: 0.3, peakPct: 98, minG: 0.6, maxG: "", compound: "", idealPsi: "", psiPerC: "" };
const STORAGE_KEY = "pressureOptions";
const STATE_CLASS = { "in window": "muted", cool: "bad-text", hot: "bad-text" };
const COLS = 8;

function loadOptions() {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}") }; }
  catch { return { ...DEFAULTS }; }
}

// laps: the compared timed laps ({ sessionId, lap, slot, shortLabel, driver }); settings: the metric definitions (for gates)
export default function PressureBox({ laps, settings }) {
  const [opts, setOpts] = useState(loadOptions);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const change = (patch) => setOpts((prev) => {
    const next = { ...prev, ...patch };
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)); } catch { /* storage full or blocked: just don't remember */ }
    return next;
  });
  const maxG = opts.maxG !== "" && Number(opts.maxG) > opts.minG ? Number(opts.maxG) : "";
  const peakOk = opts.peakPct !== "" && Number(opts.peakPct) >= 50 && Number(opts.peakPct) <= 100;
  const positive = (v) => (v !== "" && Number(v) > 0 ? Number(v) : "");
  const request = {
    scope: opts.scope, gate: opts.gate, g_basis: opts.gBasis, min_gain_pct: opts.minGain === "" ? DEFAULTS.minGain : opts.minGain,
    peak_pct: peakOk ? Number(opts.peakPct) : DEFAULTS.peakPct, min_g: opts.minG, max_g: maxG, compound: opts.compound,
    ideal_psi: positive(opts.idealPsi), psi_per_c: positive(opts.psiPerC),
  };

  const key = JSON.stringify([laps.map((l) => l.key), settings, request]);
  useEffect(() => {
    if (!laps.length) return;
    let stale = false;
    setError(null);
    api.pressure(laps, settings, request)
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

  return (
    <div className="card chart-card">
      <div className="card-head">
        <h3>Tyre pressures <span className="tag warn" title={d?.note}>est.</span></h3>
        <span className="muted">cold pressure that lands each tyre's hot pressure on the ideal, with the tread temperature · AC only</span>
      </div>
      <div className="toolbar pad damper-tools">
        <Segmented label="What to count" value={opts.scope} onChange={(scope) => change({ scope })}
          options={[{ value: "lap", label: "Each lap" }, { value: "run", label: "Whole run", title: "Every full timed lap" }]} />
        <label className="field">Only
          <Select size="sm" value={opts.gate} onChange={(e) => change({ gate: e.target.value })}>
            {GATE_OPTIONS.map((g) => <option key={g} value={g}>{g === "all" ? "the whole lap" : g}</option>)}
          </Select>
        </label>
        <Segmented label="G" value={opts.gBasis} onChange={(gBasis) => change({ gBasis })}
          options={[
            { value: "auto", label: "Auto", title: "Cornering (lateral G), except in the Braking gate, which uses combined G" },
            { value: "lateral", label: "Lateral", title: "Only lateral G sets the peak and the band: cornering, not braking or traction" },
            { value: "combined", label: "Combined", title: "Lateral and longitudinal G together: braking and traction count too" },
          ]} />
        <label className="field" title="A change is only called worthwhile if it recovers at least this many points of grip. The pressure curve is shallow (the MFT02 loses about 0.75% per psi), so a smaller gain is within what the model can tell apart.">
          Worthwhile from
          <Input size="sm" aria-label="Smallest grip gain that counts, in points" type="number" min="0" max="5" step="0.1" value={opts.minGain}
            onChange={(e) => e.target.value !== "" && change({ minGain: Number(e.target.value) })} /> pts
        </label>
        <span className="field band-inputs" title="Only count moments whose G is in this band (lateral or combined, see G). Peak G is this percentile of that G over the whole laps (below 100 ignores kerb spikes). Leave the upper limit empty for no limit.">
          <Input size="sm" aria-label="Lower share of peak G" type="number" min="0" max="1" step="0.05"
            value={opts.minG} onChange={(e) => e.target.value !== "" && change({ minG: Number(e.target.value) })} /> to
          <Input size="sm" aria-label="Upper share of peak G (empty: no limit)" type="number" min="0" max="2" step="0.05" placeholder="max"
            value={opts.maxG} onChange={(e) => change({ maxG: e.target.value })} /> × peak G · peak =
          <Input size="sm" aria-label="Percentile of G that counts as peak" type="number" min="50" max="100" step="1"
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
        <span className="field band-inputs" title="Leave empty to use the car's own numbers from tyres.ini (PRESSURE_IDEAL, PRESSURE_TEMPERATURE_GAIN). Type a number to try another: the ideal is the hot pressure with the most grip, the gain is how much the pressure rises per °C of tread temperature.">
          Ideal hot
          <Input size="sm" aria-label="Ideal hot pressure, psi (empty: from tyres.ini)" type="number" min="1" step="0.5" placeholder="file"
            value={opts.idealPsi} onChange={(e) => change({ idealPsi: e.target.value })} /> psi · gain
          <Input size="sm" aria-label="Pressure rise per °C of tread (empty: from tyres.ini)" type="number" min="0.01" step="0.01" placeholder="file"
            value={opts.psiPerC} onChange={(e) => change({ psiPerC: e.target.value })} /> psi/°C
        </span>
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
            <Plot deps={[d, sels.map((s) => s.slot).join()]} draw={(el) => drawPressure(el, d, sels)} />
            <PressureTable d={d} sels={sels} />
          </div>
        </>
      )}
    </div>
  );
}

// Per selection: each tyre's hot pressure, temperature and the cold change, then the same for a hotter or cooler tread
function PressureTable({ d, sels }) {
  return (
    <div className="table-scroll damper-table">
      {d.selections.map((s, i) => (
        <table className="data" key={i}>
          <thead>
            <tr>
              <th colSpan={COLS} className="group">
                <span className="lap-dot" style={{ background: `var(--lap-${sels[i].slot + 1})` }} /> {sels[i].label}
                <div className="muted small">
                  {num(s.time_s, 1)} s counted · peak {num(s.peak_g, 2)} g {s.g_basis} · {s.compound} · tread = {s.temp_source}
                  {s.measured_psi_per_c != null && ` · this run's pressure gain ${num(s.measured_psi_per_c, 3)} psi/°C (file: ${num(s.tyres[0].psi_per_c, 3)})`}
                </div>
              </th>
            </tr>
            <tr>
              <th>Tyre</th>
              <th className="num g" title="Average hot pressure near the limit, each moment weighted by the tyre's force (load^LS_EXPY)">Hot psi</th>
              <th className="num" title="10th to 90th percentile">Range</th>
              <th className="num" title="PRESSURE_IDEAL from tyres.ini: the hot pressure with the most grip">Ideal</th>
              <th className="num" title="Grip lost to the pressure being off the ideal, in points (linear in the distance, an assumption)">Loss</th>
              <th className="num" title="Mean tread temperature (the tyre's temperature curve is read against it), and whether it is inside the curve's 1% window">Tread °C</th>
              <th className="num" title="Grip the temperature curve gives, as a % of its peak">Temp grip</th>
              <th title="Change to the cold pressure in the setup (− = lower). The sliders move in whole steps, so the rounded change is what you can type in; after it, the grip still lost.">Cold change</th>
            </tr>
          </thead>
          <tbody>
            {s.corners.map((c) => (
              <tr key={c.corner}>
                <td>{c.corner}</td>
                <td className="num mono g">{num(c.hot_psi, 2)}</td>
                <td className="num mono">{num(c.p10_psi, 1)} to {num(c.p90_psi, 1)}</td>
                <td className="num mono">{num(c.ideal_psi, 1)}</td>
                <td className="num mono">{num(c.loss_pct, 2)}%</td>
                <td className="num mono">{num(c.tread_c, 0)} <span className={STATE_CLASS[c.temp_state]}>{c.temp_state}</span></td>
                <td className="num mono">{num(c.temp_grip_pct, 1)}%</td>
                <td className="num mono" title={c.hot_psi == null ? undefined : `Exact change ${signed(c.change_psi, 2)} psi`}>
                  {c.hot_psi == null ? "–" : c.worthwhile ? <>
                    <b>{signed(c.rounded_change_psi, 1)} psi</b>
                    <span className="muted"> · {num(c.loss_pct, 2)}% → {num(c.left_pct, 2)}%</span>
                  </> : <span className="muted">no clear change · exact {signed(c.change_psi, 2)}</span>}
                </td>
              </tr>
            ))}
          </tbody>
          <thead>
            <tr>
              <th colSpan={COLS} className="group" title="If the tread ran this much hotter (a hotter day, a longer stint, a faster car) the hot pressure rises by the car's psi per °C. Shown: the cold pressure change that would still land on the ideal. Hover a number for the hot pressure and the temperature grip.">
                Cold change if the tread ran hotter or cooler by…
              </th>
            </tr>
            <tr>
              <th>Tyre</th>
              {d.shifts_c.map((x) => <th key={x} className="num">{signed(x, 0)} °C</th>)}
              <th colSpan={COLS - 1 - d.shifts_c.length} />
            </tr>
          </thead>
          <tbody>
            {s.corners.map((c) => (
              <tr key={c.corner}>
                <td>{c.corner}</td>
                {c.scenarios.map((x, k) => (
                  <td key={k} className="num mono"
                    title={`Hot ${num(x.hot_psi, 2)} psi · temperature grip ${num(x.temp_grip_pct, 1)}% · pressure loss if the cold pressure stays: ${num(x.loss_if_unchanged_pct, 2)}%`}>
                    {signed(x.cold_change_psi, 1)}
                  </td>
                ))}
                <td colSpan={COLS - 1 - c.scenarios.length} />
              </tr>
            ))}
          </tbody>
        </table>
      ))}
      <p className="hint table-foot">Cold change: − = lower the cold pressure in the setup (add it to the number you ran). The pressure curve is shallow, so small gains are shown as "no clear change"; wear, graining and AC's thermal model aren't scored, so check the number with a back-to-back run.</p>
    </div>
  );
}
