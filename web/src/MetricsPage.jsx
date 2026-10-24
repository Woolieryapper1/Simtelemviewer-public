import { useEffect, useState } from "react";
import { api } from "./api.js";
import Arrange from "./Arrange.jsx";
import { drawBalance, drawGateMap, drawGG, drawMech } from "./charts.js";
import CamberBox from "./CamberBox.jsx";
import DamperBox from "./DamperBox.jsx";
import DamperSetupBox from "./DamperSetupBox.jsx";
import Definitions from "./Definitions.jsx";
import { fmtLap, num, shortLabel, signed } from "./format.js";
import Plot from "./Plot.jsx";
import { Badge, Button, Field, Select, Segmented } from "./ui/index.js";

// Metrics page: where each corner gate is, time in each gate, the G-G diagram,
// balance and oversteer by gate, mechanical balance (AC only) and a per-lap summary - for the compared (full
// timed) laps. The boxes are customisable like the Stint page's (add / remove /
// drag to reorder), remembered in this browser - see BOXES for what can be shown.

const hasBalance = (m) => m.laps.some((l) => l.summary.balance_deg != null);
const hasMech = (m) => m.laps.some((l) => l.summary.mech_balance_pct != null);
// title: shown in the add list; wide: takes the whole row
export const BOXES = {
  gateMap: { title: "Where each gate is", has: () => true },
  gateTable: { title: "Time by gate", has: () => true },
  gg: { title: "G-G diagram", has: () => true },
  balance: { title: "Balance by gate", has: hasBalance },
  oversteer: { title: "Oversteer by gate", has: hasBalance, wide: true },
  // always offered: for iRacing laps the box says why there's nothing, rather than staying blank
  mech: { title: "Mechanical balance (est.)", has: () => true, wide: true },
  summary: { title: "Lap summary", has: () => true, wide: true },
  dampers: { title: "Damper histograms", has: () => true, wide: true }, // fetches its own data (DamperBox.jsx)
};
const DEFAULT_BOXES = ["gateMap", "gateTable", "gg", "balance", "oversteer", "summary", "mech", "dampers"];
const STORAGE_KEY = "metricsLayout";

function loadBoxes() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null");
    if (Array.isArray(saved)) return saved.filter((id) => id in BOXES); // drop anything that no longer exists
  } catch { /* corrupt entry: fall back to defaults */ }
  return DEFAULT_BOXES;
}
export default function MetricsPage({ laps: allLaps, settings, onSettings, comparison }) {
  const laps = allLaps.filter((l) => l.kind === "timed").map((l) => ({ ...l, shortLabel: shortLabel(l) }));
  const [metrics, setMetrics] = useState(null);
  const [error, setError] = useState(null);
  const [ggColour, setGgColour] = useState("gate");
  const [boxes, setBoxes] = useState(loadBoxes);
  const [editing, setEditing] = useState(false);
  const [section, setSection] = useState("overview");

  const key = JSON.stringify([laps.map((l) => l.key), settings]);
  useEffect(() => {
    if (!laps.length) { setMetrics(null); return; }
    if (section === "dampers" || section === "camber") return;
    let stale = false;
    setError(null);
    api.metrics(laps, settings)
      .then((m) => !stale && setMetrics({ key, m }))
      .catch((e) => !stale && setError(e.message));
    return () => { stale = true; };
  }, [key, section]); // re-run when the laps, their order or the definitions change

  const m = metrics?.key === key ? metrics.m : null;
  // Show what the current definitions actually pick out on the reference lap
  const summary = m ? "reference lap: " + m.laps[0].gates.map((g) => `${g.gate} ${g.distance_pct}%`).join(" · ") : "";
  const defs = <>
    <div className="analysis-nav">
      <Segmented label="Metrics view" value={section} onChange={setSection} options={[
        { value: "overview", label: "Overview" }, { value: "balance", label: "Balance" },
        { value: "dampers", label: "Dampers" }, { value: "camber", label: "Camber" },
      ]} />
      <span className="muted">{section === "overview" ? "Corner gates, driving inputs and lap summaries"
        : section === "balance" ? "Dynamic balance and estimated mechanical balance"
        : section === "dampers" ? "Damper speeds by lap, run or corner gate, and damping ratios vs targets"
        : "Best static camber per axle from the tyre's camber curve (AC)"}</span>
    </div>
    <Definitions settings={settings} onChange={onSettings} summary={summary} />
  </>;

  if (!laps.length) return <>{defs}<div className="empty">Tick full timed laps in the drawer to compare their metrics</div></>;
  if (section === "dampers") return <>{defs}<DamperBox laps={laps} settings={settings} /><DamperSetupBox laps={laps} /></>;
  if (section === "camber") return <>{defs}<CamberBox laps={laps} settings={settings} /></>;
  if (error) return <>{defs}<div className="error">{error}</div></>;
  if (!m) return <>{defs}<div className="empty">Loading…</div></>;
  // Match each result to its compared lap (for colours and names). While new
  // numbers load the old ones stay up, so a lap may already have been removed.
  const shown = m.laps.map((r, i) => laps.find((l) => l.sessionId === r.session_id && l.lap === r.lap)
    ?? { key: `${r.session_id}#${r.lap}`, slot: i, shortLabel: `${r.driver.slice(0, 12)} L${r.lap}` });
  const slots = shown.map((l) => l.slot).join(); // redraw if a lap's colour changes

  const balanceIds = ["balance", "oversteer", "mech"];
  const inSection = (id) => id !== "dampers" && (section === "balance" ? balanceIds.includes(id) : !balanceIds.includes(id));
  const catalogue = Object.fromEntries(Object.entries(BOXES).filter(([id]) => inSection(id)));
  const visible = boxes.filter(inSection);
  const change = (ids) => {
    const next = [...boxes.filter((id) => !inSection(id)), ...ids];
    setBoxes(next);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  };
  const reset = () => change(DEFAULT_BOXES.filter(inSection));

  const render = (id) => {
    switch (id) {
      case "gateMap": return (
        <div className="card chart-card">
          <div className="card-head"><h3>Where each gate is</h3><span className="muted">reference lap</span></div>
          <Plot deps={[m, comparison?.outline]} draw={(el) => drawGateMap(el, m, comparison?.outline)} />
        </div>);
      case "gateTable": return (
        <div className="card table-card">
          <div className="card-head"><h3>Time by gate</h3><span className="muted">gates from the reference lap, so every lap is timed over the same track</span></div>
          <GateTable m={m} laps={shown} />
        </div>);
      case "gg": return (
        <div className="card chart-card">
          <div className="card-head">
            <h3>G-G diagram</h3>
            <Field label="Colour by" className="push">
              <Select size="sm" value={ggColour} onChange={(e) => setGgColour(e.target.value)}>
                <option value="gate">gate</option><option value="lap">lap</option>
              </Select>
            </Field>
          </div>
          <Plot deps={[m, ggColour, slots]} draw={(el) => drawGG(el, m, shown, ggColour)} />
        </div>);
      case "balance": return (
        <div className="card chart-card">
          <div className="card-head"><h3>Balance by gate</h3><span className="muted" title={m.balance_accuracy || undefined}>{m.balance_label || "front − rear slip angle · + understeer · − oversteer"}</span></div>
          {hasBalance(m) ? <Plot deps={[m, slots]} draw={(el) => drawBalance(el, m, shown)} />
            : <div className="box-empty">No balance measure is available for these laps. Needs slip angles or a steering-based estimate.</div>}
        </div>);
      case "oversteer": return (
        <div className="card table-card">
          <div className="card-head">
            <h3>Oversteer by gate</h3>
            <span className="muted">the average hides short slides, so: share of cornering time oversteering (balance below 0) and the worst 5%</span>
          </div>
          {hasBalance(m) ? <OversteerTable m={m} laps={shown} /> : <div className="box-empty">no balance for these laps</div>}
        </div>);
      case "mech": return (
        <div className="card table-card">
          <div className="card-head">
            <h3>Mechanical balance <span className="tag warn" title="Estimated from the logged tyre loads">est.</span></h3>
            <span className="muted">front share of the load moving across the car in steady corners (LLTD) · set by springs, bars and roll centres · more front = more understeer</span>
          </div>
          {hasMech(m) ? <>
            <MechTable m={m} laps={shown} />
            <Plot deps={[m, slots]} draw={(el) => drawMech(el, m, shown)} />
          </> : <div className="box-empty">No steady-cornering result for these laps. Mechanical balance needs all four tyre loads and enough settled cornering; iRacing does not log tyre loads.</div>}
        </div>);
      case "summary": return (
        <div className="card table-card">
          <div className="card-head"><h3>Lap summary</h3></div>
          <SummaryTable m={m} laps={shown} />
        </div>);
      case "dampers": return <DamperBox laps={laps} settings={settings} />;
      default: return null;
    }
  };

  return (
    <>
      {defs}
      <div className="section-head">
        <span className="section-tools">
          {editing && <Button icon="reset" onClick={reset} title="Back to the default boxes">Reset to default</Button>}
          <Button variant={editing ? "primary" : "secondary"} icon={editing ? "check" : "sliders"} onClick={() => setEditing(!editing)}>
            {editing ? "Done" : "Customise"}
          </Button>
        </span>
      </div>
      {editing && <p className="hint edit-hint">Drag ⋮⋮ to reorder · ✕ to remove · add more from the dashed box. Saved in this browser.</p>}
      <Arrange className="grid-2" kind="box" ids={visible} editing={editing} catalogue={catalogue} data={m}
        noData="no data for these laps" onChange={change} render={render} />
    </>
  );
}

const LapHead = ({ lap }) => (
  <><span className="lap-dot" style={{ background: `var(--lap-${lap.slot + 1})` }} /> {lap.shortLabel}</>
);

function GateTable({ m, laps }) {
  const ref = m.laps[0];
  const total = (lap) => lap.gates.reduce((a, g) => a + g.time_s, 0);
  return (
    <div className="table-scroll">
      <table className="data">
        <thead>
          <tr>
            <th colSpan={2} />
            {m.laps.map((_, i) => <th key={i} colSpan={i ? 3 : 2} className="group"><LapHead lap={laps[i]} /></th>)}
          </tr>
          <tr>
            <th>Gate</th><th className="num">% of lap</th>
            {m.laps.flatMap((_, i) => [
              <th key={`t${i}`} className="num g">Time s</th>,
              ...(i ? [<th key={`d${i}`} className="num">Δ ref</th>] : []),
              <th key={`v${i}`} className="num">Avg kph</th>,
              <th key={`g${i}`} className="num" title="G absolute (√(lat² + long²)): average · 95th percentile">G abs avg · p95</th>,
            ])}
          </tr>
        </thead>
        <tbody>
          {m.gates.map((name, g) => (
            <tr key={name}>
              <td><span className="gate-chip" style={{ "--c": `var(--gate-${g})` }}>{name}</span></td>
              <td className="num mono">{num(ref.gates[g].distance_pct)}</td>
              {m.laps.flatMap((lap, i) => {
                const gs = lap.gates[g], d = gs.time_s - ref.gates[g].time_s;
                return [
                  <td key={`t${i}`} className="num mono g">{gs.time_s.toFixed(3)}</td>,
                  ...(i ? [<td key={`d${i}`} className={`num mono ${d < 0 ? "faster" : "slower"}`}>{signed(d)}</td>] : []),
                  <td key={`v${i}`} className="num mono">{num(gs.avg_speed_kph)}</td>,
                  <td key={`g${i}`} className="num mono">{gs.g_abs_avg == null ? "–" : `${num(gs.g_abs_avg, 2)} · ${num(gs.g_abs_p95, 2)}`}</td>,
                ];
              })}
            </tr>
          ))}
          <tr className="total">
            <td>Whole lap</td><td className="num mono">100</td>
            {m.laps.flatMap((lap, i) => [
              <td key={`t${i}`} className="num mono g">{total(lap).toFixed(3)}</td>,
              ...(i ? [<td key={`d${i}`} className="num mono">{signed(total(lap) - total(ref))}</td>] : []),
              <td key={`v${i}`} />,
              <td key={`g${i}`} className="num mono">{`${num(lap.summary.g_abs_avg, 2)} · ${num(lap.summary.g_abs_p95, 2)}`}</td>,
            ])}
          </tr>
        </tbody>
      </table>
    </div>
  );
}

// Each lap's average balance per gate, with the share of cornering time oversteering and the worst 5%
function OversteerTable({ m, laps }) {
  const rows = [...m.gates.map((name, g) => ({ name, g, get: (lap) => lap.gates[g] })),
    { name: "Whole lap", get: (lap) => lap.summary }];
  return (
    <div className="table-scroll">
      <table className="data">
        <thead>
          <tr>
            <th />
            {m.laps.map((_, i) => <th key={i} colSpan={3} className="group"><LapHead lap={laps[i]} /></th>)}
          </tr>
          <tr>
            <th>Gate</th>
            {m.laps.flatMap((_, i) => [
              <th key={`a${i}`} className="num g" title="Average balance while cornering: + understeer, − oversteer">Avg °</th>,
              <th key={`o${i}`} className="num" title="Share of cornering time with balance below 0">Oversteer %</th>,
              <th key={`w${i}`} className="num" title="5th percentile: the most oversteery 5% of cornering is beyond this">Worst 5% °</th>,
            ])}
          </tr>
        </thead>
        <tbody>
          {rows.map(({ name, g, get }) => (
            <tr key={name} className={g == null ? "total" : ""}>
              <td>{g == null ? name : <span className="gate-chip" style={{ "--c": `var(--gate-${g})` }}>{name}</span>}</td>
              {m.laps.flatMap((lap, i) => {
                const s = get(lap);
                return [
                  <td key={`a${i}`} className="num mono g">{signed(s.balance_deg, 2)}</td>,
                  <td key={`o${i}`} className="num mono">{num(s.oversteer_pct, 1)}</td>,
                  <td key={`w${i}`} className="num mono">{signed(s.balance_p5_deg, 2)}</td>,
                ];
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Mechanical balance per lap: whole lap ± how much it varies corner to corner, the change from
// the reference lap, and each gate. Laps without tyre loads (iRacing) say so instead of showing dashes.
function MechTable({ m, laps }) {
  const ref = m.laps[0].summary.mech_balance_pct;
  const used = m.gates.map((_, g) => g).filter((g) => m.laps.some((l) => l.gates[g].mech_balance_pct != null));
  const withSpread = (s) => (s.mech_balance_pct == null ? "–"
    : `${num(s.mech_balance_pct, 1)}${s.mech_balance_spread == null ? "" : ` ±${num(s.mech_balance_spread, 1)}`}`);
  return (
    <div className="table-scroll">
      <table className="data">
        <thead>
          <tr>
            <th>Lap</th>
            <th className="num g" title="Front share over the whole lap · ± how much it varies corner to corner">Whole lap % front</th>
            <th className="num" title="Change from the reference lap (the first one)">vs ref</th>
            {used.map((g) => <th key={g} className="num"><span className="gate-chip" style={{ "--c": `var(--gate-${g})` }}>{m.gates[g]}</span></th>)}
          </tr>
        </thead>
        <tbody>
          {m.laps.map((lap, i) => {
            const s = lap.summary, v = s.mech_balance_pct;
            if (v == null) return (
              <tr key={i}><td><LapHead lap={laps[i]} /></td>
                <td colSpan={2 + used.length} className="muted">no tyre loads in this file (only Assetto Corsa logs them)</td></tr>
            );
            return (
              <tr key={i}>
                <td><LapHead lap={laps[i]} /></td>
                <td className="num mono g" title={`from ${s.mech_corners} corners`}>{withSpread(s)}</td>
                <td className="num mono">{i === 0 ? <span className="ref-badge">ref</span>
                  : ref == null ? "–" : `${num(ref, 1)} → ${num(v, 1)} (${signed(v - ref, 1)})`}</td>
                {used.map((g) => <td key={g} className="num mono">{withSpread(lap.gates[g])}</td>)}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const SUMMARY_COLS = [
  // G abs = G absolute, √(lat² + long²); p95 = 95th percentile, how much grip the lap used near its peak
  ["Balance °", (s) => s.balance_deg, 2, true], ["Oversteer %", (s) => s.oversteer_pct, 1], ["Worst 5% °", (s) => s.balance_p5_deg, 2, true],
  ["Mech % front (est.)", (s) => s.mech_balance_pct, 1],
  ["G abs avg", (s) => s.g_abs_avg, 2], ["G abs p95", (s) => s.g_abs_p95, 2],
  ["Max lat G", (s) => s.max_lat_g, 2],
  ["Max brake G", (s) => s.max_brake_g, 2], ["Full thr. %", (s) => s.full_throttle_pct, 1], ["Braking %", (s) => s.braking_pct, 1],
  ["Coasting %", (s) => s.coasting_pct, 1], ["Overlap %", (s) => s.overlap_pct, 1],
];

// Whether a lap's balance rests on known numbers (logged, or the car's real steering ratio)
// or on something learned from the run; hover for the details
function BasisTag({ basis, accuracy }) {
  if (!basis) return null;
  const learned = basis.includes("learned");
  return <span className={`tag${learned ? " warn" : ""}`} title={accuracy ? `${basis}\n${accuracy}` : basis} style={{ marginLeft: 6 }}>{learned ? "learned" : "known"}</span>;
}

function SummaryTable({ m, laps }) {
  const refTime = m.laps[0].lap_time_s;
  return (
    <div className="table-scroll">
      <table className="data">
        <thead>
          <tr>
            <th>Lap</th><th className="num">Time</th><th className="num">Δ ref</th>
            {SUMMARY_COLS.map(([name]) => <th key={name} className="num">{name}</th>)}
          </tr>
        </thead>
        <tbody>
          {m.laps.map((lap, i) => (
            <tr key={i}>
              <td><LapHead lap={laps[i]} /></td>
              <td className="num mono">{fmtLap(lap.lap_time_s)}</td>
              <td className="num mono">{i ? signed(lap.lap_time_s - refTime) : <Badge tone="accent">ref</Badge>}</td>
              {SUMMARY_COLS.map(([name, get, d, isSigned]) => {
                const v = get(lap.summary);
                return (
                  <td key={name} className="num mono">
                    {isSigned ? signed(v, d) : num(v, d)}
                    {name === "Balance °" && <BasisTag basis={lap.balance_basis} accuracy={lap.balance_accuracy} />}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
