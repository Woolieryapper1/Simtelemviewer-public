// Small wrappers around the backend API (see backend/fs_telemetry/main.py).

async function getJSON(url, options) {
  const r = await fetch(url, options);
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail ?? detail; } catch { /* not JSON */ }
    throw new Error(`${r.status}: ${detail}`);
  }
  return r.status === 204 ? null : r.json();
}

function lapUrl(sessionId, lap, endpoint, channels, stepM) {
  const q = new URLSearchParams(channels.map((c) => ["channels", c]));
  q.set("step_m", stepM);
  return `/api/sessions/${encodeURIComponent(sessionId)}/laps/${lap}/${endpoint}?${q}`;
}

// laps: [{sessionId, lap}] - the first is the reference
function compareUrl(endpoint, laps, sectors) {
  const q = new URLSearchParams(laps.map((l) => ["lap", `${l.sessionId}:${l.lap}`]));
  q.set("sectors", sectors);
  return `/api/${endpoint}?${q}`;
}

const sendJSON = (method, url, body) =>
  getJSON(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const api = {
  sessions: () => getJSON("/api/sessions"),
  laps: (id) => getJSON(`/api/sessions/${encodeURIComponent(id)}/laps`),
  channels: (id) => getJSON(`/api/sessions/${encodeURIComponent(id)}/channels`),
  aero: (id) => getJSON(`/api/sessions/${encodeURIComponent(id)}/aero`),
  stint: (id, settings) => getJSON(`/api/sessions/${encodeURIComponent(id)}/stint?${new URLSearchParams(settings)}`),
  metrics: (laps, settings) => {
    const q = new URLSearchParams(laps.map((l) => ["lap", `${l.sessionId}:${l.lap}`]));
    for (const [k, v] of Object.entries(settings)) q.set(k, v);
    return getJSON(`/api/metrics?${q}`);
  },
  // options: { scope, gate, low_mm_s, high_mm_s }; settings: the metric definitions (they set the gates)
  dampers: (laps, settings, options) => {
    const q = new URLSearchParams(laps.map((l) => ["lap", `${l.sessionId}:${l.lap}`]));
    for (const [k, v] of Object.entries({ ...settings, ...options })) q.set(k, v);
    return getJSON(`/api/dampers?${q}`);
  },
  // options: { scope, gate, peak_pct, min_g, max_g, compound, front_dcamber0, ... } (see backend camber.py
  // CamberSettings); settings: the metric definitions (they set the gates)
  camber: (laps, settings, options) => {
    const q = new URLSearchParams(laps.map((l) => ["lap", `${l.sessionId}:${l.lap}`]));
    for (const [k, v] of Object.entries({ ...settings, ...options })) if (v !== "" && v != null) q.set(k, v);
    return getJSON(`/api/camber?${q}`);
  },
  // options: { scope, setup_id, ls_bump, ... } (see backend damper_setup.py DamperSetupSettings)
  damperSetup: (laps, options) => {
    const q = new URLSearchParams(laps.map((l) => ["lap", `${l.sessionId}:${l.lap}`]));
    for (const [k, v] of Object.entries(options)) if (v !== "" && v != null) q.set(k, v);
    return getJSON(`/api/damper-setup?${q}`);
  },
  lapData: (id, lap, channels, stepM) => getJSON(lapUrl(id, lap, "data", channels, stepM)),
  csvUrl: (id, lap, channels, stepM) => lapUrl(id, lap, "data.csv", channels, stepM),
  compare: (laps, sectors) => getJSON(compareUrl("compare", laps, sectors)),
  compareCsvUrl: (laps, sectors) => compareUrl("compare.csv", laps, sectors),
  layouts: () => getJSON("/api/layouts"),
  // Session notes, tags, stars and lap flags, by each run's stable key (see notes.py)
  notes: () => getJSON("/api/notes"),
  noteTags: () => getJSON("/api/notes/tags"),
  editNotes: (id, body) => sendJSON("PATCH", `/api/sessions/${encodeURIComponent(id)}/notes`, body),
  editLapNotes: (id, lap, body) => sendJSON("PATCH", `/api/sessions/${encodeURIComponent(id)}/laps/${lap}/notes`, body),
  notesExportUrl: "/api/notes/export",
  importNotes: (data) => sendJSON("POST", "/api/notes/import", data),
  setups: () => getJSON("/api/setups"),
  availableSetups: () => getJSON("/api/setups/available"),
  importSetup: (path) => sendJSON("POST", "/api/setups/import", { path }),
  uploadSetup: (body) => sendJSON("POST", "/api/setups", body),
  setupNotes: (id, notes) => sendJSON("PATCH", `/api/setups/${encodeURIComponent(id)}`, { notes }),
  deleteSetup: (id) => getJSON(`/api/setups/${encodeURIComponent(id)}`, { method: "DELETE" }),
  // Setup log: dated entries per setup, also written as Markdown files (see setup_notes.py)
  setupLog: () => getJSON("/api/setups/notes"),
  setupChanges: (id) => getJSON(`/api/setups/${encodeURIComponent(id)}/changes`),
  addSetupNote: (id, body) => sendJSON("POST", `/api/setups/${encodeURIComponent(id)}/notes`, body),
  editSetupNote: (id, entryId, body) => sendJSON("PUT", `/api/setups/${encodeURIComponent(id)}/notes/${entryId}`, body),
  deleteSetupNote: (id, entryId) => getJSON(`/api/setups/${encodeURIComponent(id)}/notes/${entryId}`, { method: "DELETE" }),
  setupLogCsvUrl: "/api/setups/notes.csv",
  compareSetups: (ids) => getJSON(`/api/setups/compare?${new URLSearchParams(ids.map((id) => ["id", id]))}`),
  saveLayout: (name, panels) => getJSON(`/api/layouts/${encodeURIComponent(name)}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ panels }),
  }),
  deleteLayout: (name) => getJSON(`/api/layouts/${encodeURIComponent(name)}`, { method: "DELETE" }),
};
