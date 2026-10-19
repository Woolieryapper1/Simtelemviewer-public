import { useEffect, useState } from "react";
import { api } from "./api.js";
import { fmtLap } from "./format.js";
import { Badge, Checkbox } from "./ui/index.js";

export const MAX_LAPS = 6;
export const lapKey = (sessionId, lap) => `${sessionId}#${lap}`;

// Laps of the open run, each with a tick box to add it to the comparison.
// ✎ on a lap opens its note and its use / ignore flag (the Stint averages follow the flag).
export default function LapsBox({ session, note, selected, onToggle, onEditLap }) {
  const [laps, setLaps] = useState([]);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(null); // lap number whose note is open

  // Fetch the laps whenever another run is opened. `stale` stops a slow
  // answer for an old run from overwriting the laps of the newer one.
  useEffect(() => {
    if (!session) return;
    let stale = false;
    setLaps([]);
    setError(null);
    setEditing(null);
    api.laps(session.id)
      .then((l) => !stale && setLaps(l))
      .catch((e) => !stale && setError(e.message));
    return () => { stale = true; };
  }, [session]);

  if (!session) return <div className="box laps"><div className="box-empty">Pick a run to see its laps</div></div>;

  const full = selected.length >= MAX_LAPS;
  return (
    <div className="box laps">
      <h3 className="day">Laps <span>{session.driver}</span></h3>
      {error && <div className="error">{error}</div>}
      {laps.map((lap) => {
        const sel = selected.find((x) => x.key === lapKey(session.id, lap.number));
        const ln = note?.laps[lap.number];
        return (
          <div key={lap.number} className={`lap-item${ln?.flag === "ignore" ? " ignored" : ""}`}>
            <label className="lap" title={!sel && full ? `You can compare up to ${MAX_LAPS} laps` : ""}>
              <Checkbox aria-label={`Compare lap ${lap.number}`} checked={!!sel} disabled={!sel && full}
                onChange={() => onToggle(session, lap)} />
              {/* the lap's chart colour once it's ticked */}
              <span className="lap-dot" style={{ background: sel ? `var(--lap-${sel.slot + 1})` : "transparent" }} />
              <span className="lap-no">L{lap.number}</span>
              <span className={`lap-time mono${lap.is_best ? " best" : ""}`}>{fmtLap(lap.lap_time_s)}{lap.is_best && " ★"}</span>
              <span className="lap-tags">
                {lap.kind !== "timed" && <span className="tag">{lap.kind}</span>}
                {lap.invalid && <span className="tag bad">invalid</span>}
                {ln?.flag && <span className={`tag flag-${ln.flag}`} title="Marked by the team; the Stint averages follow it">{ln.flag}</span>}
              </span>
            </label>
              <button type="button" aria-label={`Edit lap ${lap.number} notes and flag`} className={`icon-btn small lap-edit${ln ? " has" : ""}`} aria-expanded={editing === lap.number}
                title="Note and use / ignore" onClick={(e) => { e.preventDefault(); setEditing(editing === lap.number ? null : lap.number); }}>✎</button>
            {editing === lap.number ? <LapNoteEditor lapNote={ln} onEdit={(body) => onEditLap(lap.number, body)} onDone={() => setEditing(null)} />
              : ln?.note && <div className="lap-note">{ln.note}</div>}
          </div>
        );
      })}
    </div>
  );
}

// One lap's flag (automatic / use / ignore) and note. The note saves when you leave the box.
function LapNoteEditor({ lapNote, onEdit, onDone }) {
  const [draft, setDraft] = useState(lapNote?.note ?? "");
  const [error, setError] = useState(null);
  const flag = lapNote?.flag ?? null;
  const save = (body) => onEdit(body).catch((e) => setError(e.message));
  const saveNote = () => draft !== (lapNote?.note ?? "") && save({ note: draft });
  const choices = [[null, "Auto", "Let the Laps counted filter decide"], ["use", "Use", "Always count this lap"],
    ["ignore", "Ignore", "Never count this lap"]];
  return (
    <div className="lap-editor">
      <span className="segmented" role="group" aria-label="Count this lap?">
        {choices.map(([value, label, title]) => (
          <button key={label} type="button" title={title} aria-pressed={flag === value}
            onMouseDown={(e) => e.preventDefault()} onClick={() => save({ flag: value, note: draft })}>{label}</button>
        ))}
      </span>
      <input autoFocus placeholder="Note, e.g. flat spot" value={draft} onChange={(e) => setDraft(e.target.value)} onBlur={saveNote}
        onKeyDown={(e) => { if (e.key === "Enter") { saveNote(); onDone(); } if (e.key === "Escape") onDone(); }} aria-label="Lap note" />
      {error && <div className="error">{error}</div>}
    </div>
  );
}
