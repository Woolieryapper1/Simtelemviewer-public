import { useEffect, useMemo, useState } from "react";
import { api } from "./api.js";
import Arrange from "./Arrange.jsx";
import { drawTrend } from "./charts.js";
import Definitions from "./Definitions.jsx";
import { fmtDate, fmtLap, fmtSplit, num } from "./format.js";
import LapFilterMenu, { loadLapFilter, saveLapFilter } from "./LapFilterMenu.jsx";
import { lapKey } from "./LapsBox.jsx";
import Plot from "./Plot.jsx";
import { CHARTS, TILES, defaultLayout, loadLayout, resetLayout, saveLayout } from "./stintBoxes.jsx";
import { Badge, Button, Checkbox } from "./ui/index.js";

const CORNERS = ["FL", "FR", "RL", "RR"];

// Stint page: the open run, lap by lap - summary tiles, trend charts, a lap
// table you can tick laps from, and the estimated aero map for the run.
// The tiles and charts are customisable (add / remove / drag to reorder) and
// remembered per car - see stintBoxes.jsx for everything that can be shown.
export default function StintPage({ session, note, settings, onSettings, selected, onToggleLap }) {
  const [stint, setStint] = useState(null);
  const [aero, setAero] = useState(null);
  const [error, setError] = useState(null);
  const [layout, setLayout] = useState(null); // { tiles: [ids], charts: [ids], custom }
  const [editing, setEditing] = useState(false);
  const [lapFilter, setLapFilter] = useState(loadLapFilter); // which laps count, remembered in this browser

  // Only the driver-input thresholds and the lap filter change the stint numbers.
  // The old numbers stay up while the new ones load, so changing the filter doesn't flash.
  const inputs = `${settings.full_throttle_pct},${settings.pedal_on_pct}`;
  const filterKey = `${lapFilter.include_partial},${lapFilter.slow_filter},${lapFilter.slow_pct}`;
  // Laps marked use / ignore in the drawer change which laps count too
  const flagsKey = Object.entries(note?.laps ?? {}).map(([n, l]) => `${n}${l.flag ?? ""}`).join(",");
  useEffect(() => {
    if (!session) return;
    let stale = false;
    setError(null);
    api.stint(session.id, { ...settings, ...lapFilter })
      .then((s) => !stale && setStint(s))
      .catch((e) => !stale && setError(e.message));
    return () => { stale = true; };
  }, [session?.id, inputs, filterKey, flagsKey]); // re-run for another run, other thresholds, lap filter or lap flags

  // Laps the trend charts show: out and unfinished laps only when they're counted
  const plotted = useMemo(() => stint?.laps.filter((l) => l.kind === "timed" || l.in_averages) ?? [], [stint]);

  // Each car remembers its own tiles and charts
  useEffect(() => {
    if (stint) setLayout(loadLayout(stint));
  }, [stint?.car, stint?.session_id]);

  useEffect(() => {
    if (!session) return;
    let stale = false;
    setAero(null);
    api.aero(session.id)
      .then((a) => !stale && setAero(a))
      .catch((e) => !stale && setAero({ error: e.message }));
    return () => { stale = true; };
  }, [session?.id]);

  if (!session) return <div className="empty">Pick a run in the drawer to see its stint lap by lap</div>;
  const defs = <Definitions settings={settings} onChange={onSettings} summary="driver inputs use the pedal thresholds" />;
  if (error) return <>{defs}<div className="error">{error}</div></>;
  if (!stint || stint.session_id !== session.id) return <>{defs}<div className="empty">Loading…</div></>;

  if (!layout) return <>{defs}<div className="empty">Loading…</div></>;

  const change = (next) => {
    const l = { ...layout, ...next, custom: true };
    setLayout(l);
    saveLayout(stint.car, l);
  };
  const reset = () => {
    resetLayout(stint.car);
    setLayout({ ...defaultLayout(stint), custom: false });
  };
  const changeFilter = (f) => {
    setLapFilter(f);
    saveLapFilter(f);
  };
  const partialShown = plotted.some((l) => l.kind !== "timed");

  return (
    <>
      {defs}
      <div className="section-head">
        <h2>{stint.driver}</h2>
        <span className="muted">{fmtDate(session.started)} · {stint.car}</span>
        <span className="hint">
          ● counted lap · ✕ invalid · <span title="Left out by the Laps counted menu">grey: left out</span>{partialShown && " · ○ out or unfinished"}
        </span>
        <span className="section-tools">
          <LapFilterMenu filter={lapFilter} onChange={changeFilter} summary={stint.summary} />
          {editing && <Button icon="reset" onClick={reset} title={`Back to the default tiles and charts for ${stint.car}`}>Reset to default</Button>}
          <Button variant={editing ? "primary" : "secondary"} icon={editing ? "check" : "sliders"} onClick={() => setEditing(!editing)}>
            {editing ? "Done" : "Customise"}
          </Button>
        </span>
      </div>
      {editing && <p className="hint edit-hint">Drag ⋮⋮ to reorder · ✕ to remove · add more from the dashed box. Saved for every {stint.car} run.</p>}

      <Arrange className="stats" kind="tile" ids={layout.tiles} editing={editing} catalogue={TILES} data={stint}
        onChange={(tiles) => change({ tiles })}
        render={(id) => {
          const t = TILES[id], { value, sub } = t.value(stint);
          return <Stat label={t.label} value={value} sub={t.has(stint) ? sub : "no data in this run"} />;
        }} />

      <Arrange className="grid-2" kind="chart" ids={layout.charts} editing={editing} catalogue={CHARTS} data={stint}
        onChange={(charts) => change({ charts })}
        render={(id) => {
          const c = CHARTS[id];
          return <Trend title={c.title} unit={c.unit} laps={plotted} series={c.series(plotted)} bars={c.bars}
            empty={!c.has(stint) ? "no data in this run"
              : plotted.length ? null : "no full laps in this run: include out and unfinished laps to see them"} />;
        }} />

      <StintTable stint={stint} session={session} note={note} selected={selected} onToggleLap={onToggleLap} />
      <AeroCard aero={aero} />
    </>
  );
}

export function Stat({ label, value, sub, swatch }) {
  return (
    <div className="stat">
      <div className="stat-label">{swatch && <span className="stat-swatch" style={{ background: swatch }} />}{label}</div>
      <div className="stat-value mono">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

function Trend({ title, unit, laps, series, bars = false, empty = null }) {
  return (
    <div className="card chart-card">
      <div className="card-head"><h3>{title}</h3><span className="muted">{unit}</span></div>
      {empty ? <div className="box-empty">{empty}</div>
        : <Plot deps={[laps, series.length]} draw={(el) => drawTrend(el, unit, laps, series, { bars })} />}
    </div>
  );
}

// Every lap of the run with its numbers; tick a lap to add it to the comparison
function StintTable({ stint, session, note, selected, onToggleLap }) {
  const sm = stint.summary, best = sm.best_lap_s, nSplits = sm.best_splits_s.length;
  const kers = TILES.kers.has(stint); // no KERS column for cars without it
  const groups = [...(nSplits ? [["Sectors s", nSplits]] : []), ["Fuel L", 1], ["Grip lost %", 4], ["Core temp °C", 4],
    ["Pressure psi", 4], ...(kers ? [["KERS kJ", 1]] : []), ["Driver inputs % of lap", 4]];
  return (
    <div className="card table-card">
      <div className="card-head"><h3>Laps</h3><span className="muted">tick to compare</span></div>
      <div className="table-scroll">
        <table className="data stint-table">
          <thead>
            <tr>
              <th colSpan={5} />
              {groups.map(([name, span]) => <th key={name} colSpan={span} className="group">{name}</th>)}
            </tr>
            <tr>
              <th /><th>Lap</th><th>Type</th><th className="num">Time</th><th className="num">Δ best</th>
              {Array.from({ length: nSplits }, (_, k) => <th key={k} className={`num${k ? "" : " g"}`}>S{k + 1}</th>)}
              <th className="num g">Used</th>
              {[0, 1, 2].flatMap((g) => CORNERS.map((c, i) => <th key={`${g}${c}`} className={`num${i ? "" : " g"}`}>{c}</th>))}
              {kers && <th className="num g">Deployed</th>}
              <th className="num g">Full thr.</th><th className="num">Braking</th><th className="num">Coasting</th><th className="num">Overlap</th>
            </tr>
          </thead>
          <tbody>
            {stint.laps.map((l) => {
              const rep = l.in_pace; // counts towards the lap-time numbers
              const ticked = selected.some((x) => x.key === lapKey(stint.session_id, l.number));
              return (
                <tr key={l.number} className={`${rep ? "" : "dim"}${rep && l.lap_time_s === best ? " best" : ""}`}>
                  <td><Checkbox aria-label={`Compare lap ${l.number}`} checked={ticked} onChange={() => onToggleLap(session, { number: l.number, lap_time_s: l.lap_time_s, kind: l.kind, invalid: l.invalid })} /></td>
                  <td title={note?.laps[l.number]?.note ?? ""}>L{l.number}{note?.laps[l.number]?.note && <span className="muted"> ✎</span>}</td>
                  <td><LapTags lap={l} /></td>
                  <td className="num mono">{fmtLap(l.lap_time_s)}</td>
                  <td className="num mono">{rep && best != null ? `+${(l.lap_time_s - best).toFixed(3)}` : ""}</td>
                  {Array.from({ length: nSplits }, (_, k) => {
                    const v = l.splits_s[k];
                    const isBest = rep && v != null && Math.abs(v - sm.best_splits_s[k]) < 0.0005;
                    return <td key={k} className={`num mono${k ? "" : " g"}`}><span className={isBest ? "sector s-best" : ""}>{fmtSplit(v)}</span></td>;
                  })}
                  <td className="num mono g">{num(l.fuel_used, 3)}</td>
                  {["grip_lost", "core_temp_avg", "pressure_avg"].flatMap((key, g) =>
                    CORNERS.map((c, i) => <td key={key + c} className={`num mono${i ? "" : " g"}`}>{num(l[key][c], g === 0 ? 2 : 1)}</td>))}
                  {kers && <td className="num mono g">{num(l.kers_deployed_kj, 1)}</td>}
                  <td className="num mono g">{num(l.full_throttle_pct)}</td>
                  <td className="num mono">{num(l.braking_pct)}</td>
                  <td className="num mono">{num(l.coasting_pct)}</td>
                  <td className="num mono">{num(l.overlap_pct)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// What kind of lap it was and why it's left out, if it is. A timed lap that's left
// out (too slow, first/last) gets a red tag; out and unfinished laps a plain one.
function LapTags({ lap: l }) {
  const partial = l.kind !== "timed";
  const counted = partial && l.in_averages ? "counts towards fuel and tyre averages, not lap times" : "not counted";
  return (
    <>
      {l.excluded ? <span className={`tag${partial ? "" : " bad"}`} title={`${l.excluded}: ${counted} (see Laps counted)`}>{l.excluded}</span>
        : partial && <span className="tag">{l.kind}</span>}
      {l.invalid && <span className="tag bad">invalid</span>}
      {l.flag === "use" && <span className="tag flag-use" title="Marked use in the lap list: always counted">use</span>}
    </>
  );
}

// Downforce by speed band for this run. AC doesn't log aero, so every number
// here is an ESTIMATE from the tyre loads - labelled as such everywhere.
function AeroCard({ aero }) {
  if (!aero) return null;
  return (
    <div className="card table-card">
      <div className="card-head">
        <h3>Aero map</h3><Badge tone="warn">estimated</Badge>
        {!aero.error && <span className="muted" title={aero.note}>{aero.note}</span>}
      </div>
      {aero.error ? <div className="box-empty">{aero.error}</div> : (
        <>
          <div className="stats pad">
            <Stat label="Downforce at 100 kph (est.)" value={`${num(aero.downforce_at_100kph_n, 0)} N`} />
            <Stat label="ClA (est.)" value={`${num(aero.calibration.coefficient_m2, 2)} m²`}
              sub={`± ${num(aero.calibration.cla_error_pct, 1)} %`} />
            <Stat label="Aero balance (est.)" value={`${num(aero.calibration.balance_front_pct, 1)} % front`}
              sub={`± ${num(aero.calibration.balance_error_pp, 1)} points`} />
            <Stat label="Car mass from the fit" value={`${num(aero.calibration.mass_kg, 0)} kg`}
              sub={`static ${num(aero.calibration.static_front_pct, 1)} % front`} />
            <Stat label="Fit quality" value={aero.calibration.trustworthy ? "Good" : "Doubtful"}
              sub={`± ${num(aero.calibration.fit_error_n, 0)} N over ${aero.calibration.samples} samples`} />
          </div>
          {aero.calibration.note && <p className="note">{aero.calibration.note}</p>}
          <div className="table-scroll">
            <table className="data">
              <thead>
                <tr>
                  <th>Speed kph</th><th className="num">Samples</th><th className="num">Downforce N</th><th className="num">ClA m²</th>
                  <th className="num">Balance % front</th><th className="num">Ride height F mm</th><th className="num">Ride height R mm</th>
                </tr>
              </thead>
              <tbody>
                {aero.bands.map((b) => (
                  <tr key={b.speed_kph}>
                    <td className="mono">{b.speed_kph}</td>
                    <td className="num mono">{b.samples}</td>
                    <td className="num mono">{num(b.downforce_n, 0)}</td>
                    <td className="num mono">{num(b.coefficient_m2, 2)}</td>
                    <td className="num mono">{num(b.balance_front_pct, 1)}</td>
                    <td className="num mono">{num(b.ride_height_front_mm, 1)}</td>
                    <td className="num mono">{num(b.ride_height_rear_mm, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
