import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { drawMap, resetMapZoom, sectorAt, zoomMapTo } from "./charts.js";
import { fmtLap } from "./format.js";
import Plot from "./Plot.jsx";
import { Stat } from "./StintPage.jsx";
import { Button, Checkbox, Field, Select } from "./ui/index.js";

export const SECTOR_COUNTS = [10, 15, 20, 25, 30, 40, 50];

// Match each lap of a comparison to its compared lap (for colours and names)
export function comparedLaps(cmp, laps) {
  return cmp.laps.map((r, i) => laps.find((l) => l.sessionId === r.session_id && l.lap === r.lap)
    ?? { key: `${r.session_id}#${r.lap}`, slot: i, driver: r.driver, lap: r.lap, shortLabel: `${r.driver.slice(0, 12)} L${r.lap}` });
}

// Track page: a big zoomable map with switchable layers, and the mini-sector
// report (who was quickest through each equal stretch of track).
export default function TrackPage({ laps, comparison: cmp, sectors, onSectors, colourKey }) {
  // lineColour: racing lines in each lap's colour, or by pedal (throttle / coasting / braking)
  const [opts, setOpts] = useState({ surface: true, lines: true, lineColour: "lap", colouring: "dominance", labels: false });
  const [active, setActive] = useState(null); // mini-sector zoomed to
  const mapRef = useRef(null);

  // Esc resets the map zoom
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") { resetMapZoom(mapRef.current); setActive(null); } };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const timed = laps.filter((l) => l.kind === "timed");
  if (!timed.length || !cmp) return <div className="empty">Tick a full timed lap in the drawer to see it on the map</div>;

  const shown = comparedLaps(cmp, laps).map((l) => ({ ...l, shortLabel: l.shortLabel ?? `${l.driver.slice(0, 12)} L${l.lap}` }));
  const many = shown.length > 1;
  // Fastest-lap colouring needs two laps; with one, fall back to speed
  const colouring = opts.colouring === "dominance" && !many ? "speed" : opts.colouring;
  const set = (key, value) => setOpts({ ...opts, [key]: value });
  const zoomTo = (s) => { setActive(s.number); zoomMapTo(mapRef.current, cmp, s); };
  const fastest = Math.min(...cmp.laps.map((l) => l.lap_time_s));

  return (
    <>
      <div className="toolbar">
        <Checkbox className="field" checked={opts.surface} onChange={(e) => set("surface", e.target.checked)}>Track surface</Checkbox>
        <Checkbox className="field" checked={opts.lines} onChange={(e) => set("lines", e.target.checked)}>Racing lines</Checkbox>
        <Field label="Line colour" title="Pedals: pastel green = throttle, white = coasting, pastel red = braking">
          <Select value={opts.lineColour} disabled={!opts.lines} onChange={(e) => set("lineColour", e.target.value)}>
            <option value="lap">Lap colour</option>
            <option value="pedals">Pedals (throttle · coast · brake)</option>
          </Select>
        </Field>
        <Field label="Colour by">
          <Select value={opts.colouring} onChange={(e) => set("colouring", e.target.value)}>
            <option value="dominance">Fastest lap per mini-sector</option>
            <option value="speed">Speed (reference lap)</option>
            <option value="none">Nothing</option>
          </Select>
        </Field>
        <Checkbox className="field" checked={opts.labels} onChange={(e) => set("labels", e.target.checked)}>Mini-sector numbers</Checkbox>
        <span className="push">
          <span className="hint">Scroll to zoom · drag to pan · click a mini-sector</span>
          <Button icon="reset" title="Esc" onClick={() => { resetMapZoom(mapRef.current); setActive(null); }}>Reset zoom</Button>
        </span>
      </div>
      {cmp.outline_note && <p className="note">{cmp.outline_note}</p>}

      <div className="card chart-card">
        <Plot ref={mapRef} deps={[cmp, colouring, opts, shown.map((l) => `${l.key}${l.slot}`).join(), colourKey]} draw={(el) => {
          drawMap(el, cmp, shown, { ...opts, colouring, big: true, height: Math.max(520, window.innerHeight - 260) });
          el.removeAllListeners?.("plotly_click");
          el.on("plotly_click", (e) => { const s = sectorAt(cmp, e.points[0].x, e.points[0].y); if (s) zoomTo(s); });
        }} />
      </div>

      <div className="card table-card">
        <div className="card-head">
          <h3>Mini-sectors</h3><span className="muted">quickest lap through each equal stretch of track</span>
          <span className="push">
            <Field label="Count">
              <Select size="sm" value={sectors} onChange={(e) => onSectors(Number(e.target.value))}>
                {SECTOR_COUNTS.map((n) => <option key={n} value={n}>{n}</option>)}
              </Select>
            </Field>
            <Button as="a" icon="download" href={api.compareCsvUrl(timed, sectors)} download>CSV</Button>
          </span>
        </div>
        <div className="stats pad">
          {cmp.laps.map((r, i) => (
            <Stat key={i} swatch={`var(--lap-${shown[i].slot + 1})`} label={shown[i].shortLabel}
              value={many ? `${r.sectors_won}/${cmp.sectors.length}` : fmtLap(r.lap_time_s)}
              sub={many ? `mini-sectors won · ${r.time_in_won_sectors_s.toFixed(3)} s gained in them` : `${cmp.sectors.length} mini-sectors`} />
          ))}
          {many && (
            <Stat label="Theoretical best" value={fmtLap(cmp.theoretical_best_s)}
              sub={`best of every mini-sector · ${(cmp.theoretical_best_s - fastest).toFixed(3)} s vs fastest lap`} />
          )}
        </div>
        <div className="table-scroll tall">
          <table className="data clickable">
            <thead>
              <tr>
                <th>MS</th><th className="num">From m</th>
                {shown.map((l) => <th key={l.key} className="num"><span className="lap-dot" style={{ background: `var(--lap-${l.slot + 1})` }} /> {l.shortLabel}</th>)}
                {many && <th className="num">Margin</th>}
              </tr>
            </thead>
            <tbody>
              {cmp.sectors.map((s, k) => (
                <tr key={s.number} className={active === s.number ? "active" : ""} onClick={() => zoomTo(s)}>
                  <td>{s.number}</td>
                  <td className="num mono">{s.start_m.toFixed(0)}</td>
                  {cmp.laps.map((r, i) => (
                    <td key={i} className="num mono">
                      <span className={many && s.winner === i ? "sector s-best" : ""}>{r.sector_times_s[k].toFixed(3)}</span>
                    </td>
                  ))}
                  {many && <td className="num mono">{s.margin_s.toFixed(3)} s</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
