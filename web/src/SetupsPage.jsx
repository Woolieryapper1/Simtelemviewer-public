import { useEffect, useState } from "react";
import { api } from "./api.js";
import { fmtDate } from "./format.js";
import { Badge, Button, Checkbox, Input, Textarea } from "./ui/index.js";

// Setups page: the team's setup library (stored on the server) on the left,
// and a side-by-side comparison of the ticked setups on the right.
export default function SetupsPage() {
  const [setups, setSetups] = useState([]);
  const [picked, setPicked] = useState([]); // ids in the order ticked; first = reference
  const [filter, setFilter] = useState("");
  const [available, setAvailable] = useState(null); // setups in the AC folder, null = import box closed
  const [editing, setEditing] = useState(null); // { id, notes } while editing notes
  const [message, setMessage] = useState(null);
  const [log, setLog] = useState({ folder: "", entries: {} }); // the dated setup log, by setup id
  const [logOpen, setLogOpen] = useState(null); // id of the setup whose log is showing

  async function load() {
    try {
      const [all, notes] = await Promise.all([api.setups(), api.setupLog()]);
      setSetups(all);
      setLog(notes);
      const ids = new Set(all.map((x) => x.id));
      setPicked((p) => p.filter((id) => ids.has(id))); // forget deleted ones
    } catch (e) { setMessage(e.message); }
  }
  useEffect(() => { load(); }, []);

  // ---- import from the AC setups folder, or upload .ini files ----
  async function openImport() {
    if (available) return setAvailable(null);
    try { setAvailable(await api.availableSetups()); } catch (e) { setMessage(e.message); }
  }
  async function importOne(a) {
    try { await api.importSetup(a.path); setMessage(`Imported ${a.name}`); } catch (e) { setMessage(e.message); }
  }
  async function importAll() {
    for (const a of available.filter((x) => !x.in_library)) await importOne(a);
    await load();
    setAvailable(await api.availableSetups());
  }
  async function upload(files) {
    for (const file of files) {
      const name = file.name.replace(/\.ini$/i, "");
      try {
        await api.uploadSetup({ name, ini_text: await file.text(), file_modified: new Date(file.lastModified).toISOString() });
        setMessage(`Uploaded ${name}`);
      } catch (e) { setMessage(`${file.name}: ${e.message}`); }
    }
    await load();
  }

  // ---- notes and deleting ----
  async function saveNotes() {
    try { await api.setupNotes(editing.id, editing.notes); setEditing(null); await load(); } catch (e) { setMessage(e.message); }
  }
  async function remove(x) {
    if (!window.confirm(`Delete ${x.name} v${x.version} from the library for everyone?`)) return;
    try { await api.deleteSetup(x.id); await load(); } catch (e) { setMessage(e.message); }
  }

  const q = filter.toLowerCase().trim();
  const shown = setups
    .filter((x) => !q || `${x.name} ${x.car} ${x.track}`.toLowerCase().includes(q))
    .sort((a, b) => a.car.localeCompare(b.car) || a.name.localeCompare(b.name) || b.version - a.version);
  const toggle = (id) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));

  return (
    <div className="setups">
      <div className="card setups-lib">
        <div className="card-head">
          <h3>Setup library</h3>
          <span className="push">
            <a className="btn" href={api.setupLogCsvUrl} download title="Every log entry, one row each: opens in Excel">Log as CSV</a>
            <Button icon="download" aria-expanded={!!available} onClick={openImport}>Import setups</Button>
            <Button as="label" icon="upload">Upload .ini<input type="file" accept=".ini" multiple hidden
              onChange={(e) => { upload([...e.target.files]); e.target.value = ""; }} /></Button>
          </span>
        </div>
        {message && <div className="note">{message}</div>}
        {log.folder && <p className="note">The log is also saved as readable Markdown files (they open in Obsidian) in <span className="mono">{log.folder}</span></p>}

        {available && (
          <div className="import-box">
            <div className="import-head">
              <strong>Setups in the AC folder and in iRacing runs</strong>
              {available.some((a) => !a.in_library) && <Button size="sm" onClick={importAll}>Import all new</Button>}
            </div>
            {available.map((a) => (
              <div key={a.path} className="import-row">
                <span className="grow" title={a.path}>{a.source === "iracing" && "iRacing · "}{a.car} · {a.track} · {a.name}
                  {a.source === "iracing" && <span className="muted"> · run {fmtDate(a.modified)}</span>}</span>
                {a.in_library ? <span className="muted">in library</span>
                  : <Button size="sm" onClick={async () => { await importOne(a); await load(); setAvailable(await api.availableSetups()); }}>Import</Button>}
              </div>
            ))}
            {!available.length && <div className="box-empty">No setups found in the AC setups folder or in iRacing runs on the server</div>}
          </div>
        )}

        <div className="pad"><Input type="search" className="wide" placeholder="Filter by name, car or track" aria-label="Filter setups" value={filter}
          onChange={(e) => setFilter(e.target.value)} /></div>
        <div className="setup-list">
          {!setups.length && <div className="box-empty">The library is empty. Import setups from the AC folder or iRacing runs, or upload .ini files.</div>}
          {shown.map((x, i) => {
            const order = picked.indexOf(x.id);
            return (
              <div key={x.id}>
                {x.car !== shown[i - 1]?.car && <div className="car-head">{x.car}</div>}
                <div className={`setup-item${order >= 0 ? " picked" : ""}`}>
                  <Checkbox checked={order >= 0} onChange={() => toggle(x.id)} aria-label={`Compare ${x.name}`} />
                  <div className="setup-main">
                    <div>
                      <span className="setup-name">{x.name}</span> <span className="tag">v{x.version}</span>
                      {log.entries[x.id]?.length > 0 && <span className="tag">{log.entries[x.id].length} in log</span>}
                      {order >= 0 && <span className="muted"> · {order === 0 ? "reference" : `#${order + 1}`}</span>}
                    </div>
                    <div className="setup-meta" title={`Added ${fmtDate(x.saved_at)} · ${x.source}`}>
                      {x.track} · {x.file_modified ? `saved ${fmtDate(x.file_modified)}` : `added ${fmtDate(x.saved_at)}`}
                    </div>
                    {editing?.id === x.id ? (
                      <div className="notes-edit">
                        <Textarea autoFocus rows={3} value={editing.notes} aria-label="Notes" onChange={(e) => setEditing({ ...editing, notes: e.target.value })} />
                        <Button variant="primary" onClick={saveNotes}>Save</Button>
                        <Button onClick={() => setEditing(null)}>Cancel</Button>
                      </div>
                    ) : x.notes && <div className="setup-notes">{x.notes}</div>}
                    {logOpen === x.id && <SetupLog setup={x} entries={log.entries[x.id] ?? []} onChange={load} onError={setMessage} />}
                  </div>
                  <div className="setup-actions">
                    <button type="button" className="btn small" aria-expanded={logOpen === x.id}
                      onClick={() => setLogOpen(logOpen === x.id ? null : x.id)}>Log</button>
                    <Button size="sm" icon="note" onClick={() => setEditing({ id: x.id, notes: x.notes })}>Notes</Button>
                    <Button size="sm" variant="danger" icon="trash" onClick={() => remove(x)}>Delete</Button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <SetupComparison ids={picked} />
    </div>
  );
}

const NO_ENTRY = { date: "", author: "", changed: "", why: "", feedback: "" };

// The dated log of one setup: what changed, why, what the driver said. Above the entries, the
// settings that differ from the version before are listed automatically.
function SetupLog({ setup, entries, onChange, onError }) {
  const [changes, setChanges] = useState([]);
  const [form, setForm] = useState(null); // { id?, ...fields } while adding or editing
  useEffect(() => { api.setupChanges(setup.id).then(setChanges).catch(() => setChanges([])); }, [setup.id]);

  async function save() {
    const { id, ...body } = form;
    try {
      if (id) await api.editSetupNote(setup.id, id, body); else await api.addSetupNote(setup.id, body);
      setForm(null);
      await onChange();
    } catch (e) { onError(e.message); }
  }
  async function remove(e) {
    if (!window.confirm("Delete this log entry?")) return;
    try { await api.deleteSetupNote(setup.id, e.id); await onChange(); } catch (err) { onError(err.message); }
  }
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const empty = !(form?.changed.trim() || form?.why.trim() || form?.feedback.trim());

  return (
    <div className="setup-log">
      {changes.length > 0 && (
        <details className="log-changes">
          <summary>{changes.length} setting{changes.length === 1 ? "" : "s"} changed from the version before</summary>
          {changes.map((c) => <div key={c.label}>{c.label}: <span className="mono">{c.before ?? "–"} → {c.after ?? "–"}</span></div>)}
        </details>
      )}
      {entries.map((e) => (
        <div key={e.id} className="log-entry">
          <div className="log-head">
            <strong>{fmtDate(e.date)}</strong>{e.author && <span className="muted"> · {e.author}</span>}
            <span className="push">
              <button type="button" className="btn small" onClick={() => setForm({ ...NO_ENTRY, ...e })}>Edit</button>
              <button type="button" className="btn small" onClick={() => remove(e)}>Delete</button>
            </span>
          </div>
          {[["What changed", e.changed], ["Why", e.why], ["Driver said", e.feedback]].map(([label, text]) => text && (
            <div key={label} className="setup-notes"><span className="muted">{label}: </span>{text}</div>
          ))}
        </div>
      ))}
      {!entries.length && !form && <div className="muted">No entries yet.</div>}
      {form ? (
        <div className="log-form">
          <input type="date" value={form.date} onChange={set("date")} aria-label="Date (today if empty)" />
          <input type="text" placeholder="Who (optional)" value={form.author} onChange={set("author")} maxLength={60} />
          <textarea rows={2} placeholder="What changed" value={form.changed} onChange={set("changed")} autoFocus />
          <textarea rows={2} placeholder="Why" value={form.why} onChange={set("why")} />
          <textarea rows={2} placeholder="What the driver said" value={form.feedback} onChange={set("feedback")} />
          <div>
            <button type="button" className="btn primary" disabled={empty} onClick={save}>Save entry</button>
            <button type="button" className="btn" onClick={() => setForm(null)}>Cancel</button>
          </div>
        </div>
      ) : <button type="button" className="btn small" onClick={() => setForm({ ...NO_ENTRY })}>Add entry</button>}
    </div>
  );
}

function SetupComparison({ ids }) {
  const [cmp, setCmp] = useState(null);
  const [error, setError] = useState(null);
  const [onlyChanged, setOnlyChanged] = useState(false);
  const key = ids.join(",");

  useEffect(() => {
    if (ids.length < 2) { setCmp(null); return; }
    let stale = false;
    setError(null);
    api.compareSetups(ids).then((c) => !stale && setCmp(c)).catch((e) => !stale && setError(e.message));
    return () => { stale = true; };
  }, [key]);

  const cars = new Set(cmp?.setups.map((x) => x.car));
  let group = null;
  const rows = [];
  for (const r of cmp?.rows ?? []) {
    if (onlyChanged && !r.changed) continue;
    if (r.group !== group) {
      group = r.group;
      rows.push(<tr key={`g-${group}`}><th className="group-row" colSpan={cmp.setups.length + 1}>{group}</th></tr>);
    }
    const ref = r.values[0];
    rows.push(
      <tr key={r.field} className={r.changed ? "changed" : ""}>
        <td title={r.field}>{r.label}</td>
        {r.values.map((v, i) => {
          const differs = i > 0 && JSON.stringify(v) !== JSON.stringify(ref);
          const d = differs && typeof v === "number" && typeof ref === "number" ? v - ref : null;
          return (
            <td key={i} className={`num mono${differs ? " diff" : ""}`}>
              {v == null ? "–" : String(v)}
              {d != null && <span className={`delta ${d > 0 ? "up" : "down"}`}>{d > 0 ? "▲ +" : "▼ "}{+d.toFixed(3)}</span>}
            </td>
          );
        })}
      </tr>,
    );
  }

  return (
    <div className="card table-card">
      <div className="card-head">
        <h3>Comparison</h3>
        {cmp && <span className="muted">{cmp.changed_count} of {cmp.rows.length} fields differ{cars.size > 1 && <span className="bad-text"> · different cars</span>}</span>}
        <Checkbox className="field push" checked={onlyChanged} onChange={(e) => setOnlyChanged(e.target.checked)}>Only differences</Checkbox>
      </div>
      {error && <div className="error">{error}</div>}
      {ids.length < 2 ? (
        <div className="box-empty">Tick two or more setups to compare them. The first you tick is the reference.</div>
      ) : cmp && (
        <div className="table-scroll">
          <table className="data setup-table">
            <thead>
              <tr>
                <th>Field</th>
                {cmp.setups.map((x, i) => <th key={x.id} className="num">{x.name} v{x.version}{i === 0 && <Badge tone="accent">ref</Badge>}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.length ? rows : <tr><td colSpan={cmp.setups.length + 1} className="muted">These setups are identical</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      <p className="note">AC values are its setup-screen numbers (clicks for most fields); real units need the car's data/setup.ini.
        iRacing values are in real units, as in its garage.</p>
    </div>
  );
}
