import { useEffect, useState } from "react";
import { api } from "./api.js";
import { num, signed } from "./format.js";
import { Input, Segmented, Select } from "./ui/index.js";

// Damper optimiser box on the Metrics page, under the damper histograms (AC): damping
// ratios per axle from the car's files and a library setup, the settings that hit the
// target ratios, and each tyre's load variation to judge a change back to back.
// Its options are remembered in this browser.

const DEFAULTS = { scope: "run", setupId: "", ls_bump: 0.6, ls_rebound: 0.9, hs_bump: 0.3, hs_rebound: 0.45 };
const STORAGE_KEY = "damperSetupOptions";
const TARGETS = [["ls_bump", "LS bump"], ["ls_rebound", "LS rebound"], ["hs_bump", "HS bump"], ["hs_rebound", "HS rebound"]];

function loadOptions() {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}") }; }
  catch { return { ...DEFAULTS }; }
}

// laps: the compared timed laps ({ sessionId, lap, slot, shortLabel, driver })
export default function DamperSetupBox({ laps }) {
  const [opts, setOpts] = useState(loadOptions);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [setups, setSetups] = useState([]);

  const change = (patch) => setOpts((prev) => {
    const next = { ...prev, ...patch };
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)); } catch { /* storage full or blocked: just don't remember */ }
    return next;
  });
  useEffect(() => { api.setups().then(setSetups).catch(() => setSetups([])); }, []);

  const d0 = result?.d;
  const forCar = setups.filter((s) => s.car === d0?.car);
  // A remembered setup from another car would be refused: only send one for this car
  const setupId = forCar.some((s) => s.id === opts.setupId) ? opts.setupId : "";
  const request = { scope: opts.scope, ...Object.fromEntries(TARGETS.map(([k]) => [k, opts[k]])), ...(setupId ? { setup_id: setupId } : {}) };
  const key = JSON.stringify([laps.map((l) => l.key), request]);
  useEffect(() => {
    if (!laps.length) return;
    let stale = false;
    setError(null);
    api.damperSetup(laps, request)
      .then((d) => !stale && setResult({ key, d }))
      .catch((e) => !stale && setError(e.message));
    return () => { stale = true; };
  }, [key]);

  const d = result?.key === key ? result.d : null;
  const label = (s, i) => {
    const lap = laps.find((l) => l.sessionId === s.session_id && (s.lap == null || l.lap === s.lap));
    const who = (lap?.driver ?? s.driver).slice(0, 12);
    return { slot: lap?.slot ?? i, text: s.lap == null ? `${who} run (${s.laps.length} laps)` : (lap?.shortLabel ?? `${who} L${s.lap}`) };
  };

  return (
    <div className="card table-card">
      <div className="card-head">
        <h3>Damper optimiser <span className="tag warn" title={d?.note}>est.</span></h3>
        <span className="muted">damping ratios from the car files and setup, and what hits the targets · AC only</span>
      </div>
      <div className="toolbar pad damper-tools">
        <label className="field">Setup
          <Select size="sm" value={setupId} onChange={(e) => change({ setupId: e.target.value })}>
            <option value="">car defaults</option>
            {forCar.map((s) => <option key={s.id} value={s.id}>{s.name} · {s.track}</option>)}
          </Select>
        </label>
        {d0 && !forCar.length && <span className="muted">Import this car's setups on the Setups page to pick one</span>}
        <span className="field band-inputs" title="Target damping ratios (rules of thumb). LS = body control, HS = bumps and kerbs.">
          Targets ζ {TARGETS.map(([k, name]) => (
            <span key={k}>{name} <Input size="sm" aria-label={`Target ${name} damping ratio`} type="number" min="0.05" max="2" step="0.05"
              value={opts[k]} onChange={(e) => Number(e.target.value) > 0 && change({ [k]: Number(e.target.value) })} /></span>
          ))}
        </span>
        <Segmented label="What to check" value={opts.scope} onChange={(scope) => change({ scope })}
          options={[{ value: "lap", label: "Each lap" }, { value: "run", label: "Whole run", title: "Every full timed lap" }]} />
      </div>
      {error && <div className="error pad">{error}</div>}
      {!d && !error && <div className="box-empty">Loading…</div>}
      {d && !error && (
        <>
          <div className="note">{d.missing || `${d.note} Numbers for ${d.setup}.`}</div>
          {d.axles.map((a) => <AxleTable key={a.axle} a={a} />)}
          <CheckTable d={d} label={label} />
        </>
      )}
    </div>
  );
}

function AxleTable({ a }) {
  return (
    <div className="table-scroll">
      <p className="hint table-foot">
        <b>{a.axle}</b> · sprung {num(a.sprung_kg, 0)} kg + {num(a.unsprung_kg, 0)} kg per wheel · spring {num(a.spring_n_mm, 0)} N/mm ·
        bar {num(a.arb_n_mm, 0)} N/mm · tyre {num(a.tyre_n_mm, 0)} N/mm · ride {num(a.ride_hz, 2)} Hz · roll {num(a.roll_hz, 2)} Hz ·
        wheel hop {num(a.hop_hz, 1)} Hz{a.fast_bump_m_s != null && ` · fast above ${num(a.fast_bump_m_s * 1000, 0)} mm/s bump, ${num((a.fast_rebound_m_s ?? 0) * 1000, 0)} mm/s rebound`}
      </p>
      <table className="data">
        <thead>
          <tr>
            <th>Damper</th>
            <th className="num" title="Damping at the wheel">N·s/m</th>
            <th className="num g" title="Damping ratio in heave (ride)">ζ ride</th>
            <th className="num" title="Damping ratio in roll (the bar adds stiffness, so it's lower)">ζ roll</th>
            <th className="num" title="Target ride damping ratio">Target ζ</th>
            <th className="num">Setup now</th>
            <th className="num" title="Setting that gives the target, on the setup's steps and inside its range">Suggested</th>
          </tr>
        </thead>
        <tbody>
          {a.dampers.map((r) => (
            <tr key={r.name}>
              <td>{r.name}</td>
              <td className="num mono">{num(r.ns_m, 0)}</td>
              <td className={`num mono g ${Math.abs(r.zeta_ride - r.target_zeta) > 0.25 * r.target_zeta ? "bad-text" : ""}`}>{num(r.zeta_ride, 2)}</td>
              <td className="num mono">{num(r.zeta_roll, 2)}</td>
              <td className="num mono">{num(r.target_zeta, 2)}</td>
              <td className="num mono">{r.current}</td>
              <td className="num mono">{r.suggested == null ? <span className="muted" title="Not in this car's setup">fixed</span>
                : <>{r.suggested} <span className="muted">({signed(r.change, 0)})</span></>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Per run or lap: each tyre's load variation and time past the fast damping threshold
function CheckTable({ d, label }) {
  const corners = ["FL", "FR", "RL", "RR"];
  return (
    <div className="table-scroll">
      <table className="data">
        <thead>
          <tr>
            <th title="Compare a change back to back: less load variation = steadier grip">Check</th>
            {corners.map((c) => <th key={c} colSpan={2} className="group">{c}</th>)}
          </tr>
          <tr>
            <th />
            {corners.flatMap((c) => [
              <th key={`l${c}`} className="num g" title="RMS of tyre load about its 1 s average, % of the average, at speed">Load var %</th>,
              <th key={`f${c}`} className="num" title="% of time past the fast threshold: bump / rebound">Fast % b / r</th>,
            ])}
          </tr>
        </thead>
        <tbody>
          {d.selections.map((s, i) => {
            const l = label(s, i);
            return (
              <tr key={i}>
                <td><span className="lap-dot" style={{ background: `var(--lap-${l.slot + 1})` }} /> {l.text}</td>
                {s.corners.flatMap((c) => [
                  <td key={`l${c.corner}`} className="num mono g">{num(c.load_variation_pct, 1)}</td>,
                  <td key={`f${c.corner}`} className="num mono">{num(c.fast_bump_pct, 1)} / {num(c.fast_rebound_pct, 1)}</td>,
                ])}
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="hint table-foot">Load variation: how much each tyre's load wobbles at speed. Lower is steadier grip; compare runs before and after a damper change.</p>
    </div>
  );
}
