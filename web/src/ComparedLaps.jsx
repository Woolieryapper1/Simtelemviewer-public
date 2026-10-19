import { fmtDay, fmtLap, fmtSplit, fmtTime } from "./format.js";
import { Badge, IconButton } from "./ui/index.js";

// F1-style colour for one sector time:
//   purple = quickest of all the compared laps
//   green  = this driver's own quickest among their compared laps
//   yellow = slower than both
// With only one lap there's nothing to compare, so no colour.
function sectorClass(v, k, lap, laps, comparison) {
  if (v == null || laps.length < 2) return "";
  if (Math.abs(v - comparison.best_splits_s[k]) < 0.0005) return "s-best";
  const own = comparison.laps.filter((r) => r.driver === lap.driver).map((r) => r.splits_s[k]);
  return Math.abs(v - Math.min(...own)) < 0.0005 ? "s-pb" : "s-slow";
}

// The timing rows for the compared laps, shown at the top of every page.
// The first lap is the reference; clicking another row makes it the reference.
export default function ComparedLaps({ laps, comparison, error, onReference, onRemove }) {
  if (!laps.length) return <div className="card compared empty-card">Tick laps in the drawer to compare them</div>;

  const ref = laps[0];
  const resultOf = (lap) => comparison?.laps.find((r) => r.session_id === lap.sessionId && r.lap === lap.lap);
  const nSectors = comparison?.best_splits_s.length ?? 0;
  const manyCars = new Set(laps.map((l) => l.car)).size > 1;

  return (
    <div className="card compared">
      {laps.map((lap, i) => {
        const splits = resultOf(lap)?.splits_s ?? [];
        const gap = lap.lapTime - ref.lapTime;
        return (
          <div key={lap.key} className="timing-row" aria-current={i === 0}
            tabIndex={i > 0 ? 0 : undefined}
            onKeyDown={(e) => { if (e.target === e.currentTarget && i > 0 && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onReference(lap.key); } }}
            onClick={() => i > 0 && onReference(lap.key)} title={i > 0 ? "Click to make this the reference" : ""}>
            <span className="lap-swatch" style={{ background: `var(--lap-${lap.slot + 1})` }} />
            <span className="who">
              <span className="who-name">{lap.driver}</span>
              <span className="who-sub">
                L{lap.lap} · {fmtDay(lap.started)} {fmtTime(lap.started)}{manyCars && ` · ${lap.car}`}
              </span>
            </span>
            {i === 0 && <Badge tone="accent">ref</Badge>}
            {lap.kind !== "timed" && <Badge>{lap.kind}</Badge>}
            {lap.invalid && <Badge tone="bad">invalid</Badge>}
            <span className="sectors">
              {Array.from({ length: nSectors }, (_, k) => (
                <span key={k} className={`sector mono ${sectorClass(splits[k], k, lap, laps, comparison)}`}>
                  {fmtSplit(splits[k])}
                </span>
              ))}
            </span>
            <span className="lap-total mono">{fmtLap(lap.lapTime)}</span>
            <span className={`gap mono${gap > 0 ? " slower" : ""}`}>
              {i === 0 ? "" : `${gap >= 0 ? "+" : ""}${gap.toFixed(3)}`}
            </span>
            <IconButton icon="x" size="sm" label="Remove lap"
              onClick={(e) => { e.stopPropagation(); onRemove(lap.key); }} />
          </div>
        );
      })}
      {/* Quickest sector of each, added up */}
      {nSectors > 0 && comparison.laps.length > 1 && (
        <div className="timing-row theoretical">
          <span className="lap-swatch" />
          <span className="who"><span className="who-name">Theoretical best</span></span>
          <span className="sectors">
            {comparison.best_splits_s.map((v, k) => <span key={k} className="sector mono">{fmtSplit(v)}</span>)}
          </span>
          <span className="lap-total mono">{fmtLap(comparison.theoretical_best_sectors_s)}</span>
          <span className="gap" />
          <span className="timing-pad" />
        </div>
      )}
      {error && <div className="error">{error}</div>}
    </div>
  );
}
