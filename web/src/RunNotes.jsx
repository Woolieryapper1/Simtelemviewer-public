import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";

// Notes for the open run: a star, tags and free text. Saved to the FS Telemetry
// folder on the server (see backend notes.py), filed under the run itself rather
// than its file, so they survive the run being exported again.
// Also where the notes file is exported / imported, to share notes between PCs.
export default function RunNotes({ session, note, notes, onEdit, onImported }) {
  const [draft, setDraft] = useState(note?.notes ?? "");
  const [tagDraft, setTagDraft] = useState("");
  const [suggested, setSuggested] = useState([]);
  const [message, setMessage] = useState(null);
  const fileRef = useRef(null);

  // Another run opened, or the notes came back from the server: show what's saved
  useEffect(() => { setDraft(note?.notes ?? ""); setMessage(null); }, [session.key, note?.notes]);
  // Tag suggestions: tags in use (most used first), then common ones
  const tagCount = Object.keys(notes).length;
  useEffect(() => { api.noteTags().then(setSuggested).catch(() => {}); }, [tagCount, note?.tags?.join()]);

  const tags = note?.tags ?? [];
  const save = (body) => onEdit(session, body).catch((e) => setMessage(e.message));
  const addTag = (t) => {
    const clean = t.trim().toLowerCase();
    setTagDraft("");
    if (clean && !tags.includes(clean)) save({ tags: [...tags, clean] });
  };
  const saveText = () => { if (draft !== (note?.notes ?? "")) save({ notes: draft }); };

  async function importFile(file) {
    try {
      const r = await api.importNotes(JSON.parse(await file.text()));
      setMessage(`Imported: ${r.added} new, ${r.updated} updated, ${r.kept} kept (newer here)`);
      onImported();
    } catch (e) { setMessage(e instanceof SyntaxError ? "That file isn't a notes export" : e.message); }
  }

  return (
    <details className="box run-notes">
      <summary className="day">
        Run notes and tags
        <button type="button" className={`star${note?.star ? " on" : ""}`} aria-pressed={!!note?.star}
          title={note?.star ? "Starred: click to unstar" : "Star this run"} onClick={(e) => { e.preventDefault(); save({ star: !note?.star }); }}>
          {note?.star ? "★" : "☆"}
        </button>
      </summary>
      <div className="pad notes-body">
        <div className="chips">
          {tags.map((t) => (
            <span key={t} className="chip on">{t}
              <button type="button" aria-label={`Remove tag ${t}`} onClick={() => save({ tags: tags.filter((x) => x !== t) })}>×</button>
            </span>
          ))}
          <input className="tag-input" list="tag-suggestions" placeholder="+ tag" value={tagDraft}
            onChange={(e) => setTagDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addTag(tagDraft); } }}
            onBlur={() => tagDraft && addTag(tagDraft)} aria-label="Add a tag" />
          <datalist id="tag-suggestions">{suggested.filter((t) => !tags.includes(t)).map((t) => <option key={t} value={t} />)}</datalist>
        </div>
        {!tags.length && (
          <div className="chips">
            {suggested.slice(0, 5).map((t) => (
              <button key={t} type="button" className="chip plain" onClick={() => addTag(t)}>+ {t}</button>
            ))}
          </div>
        )}
        <textarea rows={3} placeholder="e.g. new rear wing, cold tyres" value={draft}
          onChange={(e) => setDraft(e.target.value)} onBlur={saveText} aria-label="Run notes" />
        {message && <div className="hint">{message}</div>}
        <div className="notes-share">
          <a href={api.notesExportUrl} download title="Every run's notes, as one file to import on another PC">Export notes</a>
          <button type="button" className="link" onClick={() => fileRef.current?.click()}
            title="Add notes exported on another PC. Where both have notes for a run, the one edited last wins">Import notes</button>
          <input ref={fileRef} type="file" accept=".json,application/json" hidden
            onChange={(e) => { if (e.target.files[0]) importFile(e.target.files[0]); e.target.value = ""; }} />
        </div>
      </div>
    </details>
  );
}
