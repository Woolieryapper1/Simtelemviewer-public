import { useState } from "react";

// Searching the run list: free text (driver, car, track, file name, notes, lap
// notes and tags), starred runs, tags, and under "More" the sim, a date range and
// a best-lap limit. Everything is filtered here in the browser, so it's instant.
export const EMPTY_SEARCH = { text: "", star: false, tags: [], sim: "", from: "", to: "", bestUnder: "" };
const SIMS = { ac: "Assetto Corsa", iracing: "iRacing", csv: "CSV" };

export const isSearching = (f) =>
  !!(f.text.trim() || f.star || f.tags.length || f.from || f.to || parseLapTime(f.bestUnder));

// "1:23.4" or "83.4" -> 83.4 seconds; anything else -> null
export function parseLapTime(text) {
  const m = String(text).trim().match(/^(?:(\d+):)?(\d+(?:\.\d+)?)$/);
  return m ? Number(m[1] ?? 0) * 60 + Number(m[2]) : null;
}

// Does run `s` (with its notes, or undefined) pass the search?
export function matchesSearch(s, note, f) {
  if (f.star && !note?.star) return false;
  if (f.tags.length && !f.tags.every((t) => note?.tags.includes(t))) return false;
  if (f.sim && s.source !== f.sim) return false;
  const day = s.started?.slice(0, 10) ?? ""; // "2026-10-01": compares as text
  if (f.from && (!day || day < f.from)) return false;
  if (f.to && (!day || day > f.to)) return false;
  const limit = parseLapTime(f.bestUnder);
  if (limit && !(s.best_lap_s != null && s.best_lap_s <= limit)) return false;
  const words = f.text.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const lapNotes = Object.values(note?.laps ?? {}).map((l) => l.note);
  const text = [s.id, s.driver, s.car, s.track, SIMS[s.source], note?.notes, ...(note?.tags ?? []), ...lapNotes]
    .join(" ").toLowerCase();
  return words.every((w) => text.includes(w)); // every word somewhere, in any order
}

export default function RunSearch({ search, onChange, sessions, notes }) {
  const [more, setMore] = useState(!!( search.from || search.to || search.bestUnder));
  const set = (key, value) => onChange({ ...search, [key]: value });
  const toggleTag = (t) => set("tags", search.tags.includes(t) ? search.tags.filter((x) => x !== t) : [...search.tags, t]);

  // Tags in use on these runs, most used first
  const counts = {};
  for (const s of sessions) for (const t of notes[s.key]?.tags ?? []) counts[t] = (counts[t] ?? 0) + 1;
  const tags = Object.keys(counts).sort((a, b) => counts[b] - counts[a] || a.localeCompare(b));
  const anyStar = sessions.some((s) => notes[s.key]?.star);
  const bestBad = search.bestUnder.trim() && !parseLapTime(search.bestUnder);

  return (
    <div className="run-search">
      <input type="search" className="wide" placeholder="Search runs, notes and tags" value={search.text}
        onChange={(e) => set("text", e.target.value)} aria-label="Search runs" />
      <div className="chips">
        {(anyStar || search.star) && (
          <button type="button" className="chip" aria-pressed={search.star} onClick={() => set("star", !search.star)}>★ Starred</button>
        )}
        {tags.map((t) => (
          <button key={t} type="button" className="chip" aria-pressed={search.tags.includes(t)} onClick={() => toggleTag(t)}>{t}</button>
        ))}
        <button type="button" className="chip plain" aria-expanded={more} onClick={() => setMore(!more)}>{more ? "Less" : "More…"}</button>
        {isSearching(search) && <button type="button" className="chip plain" onClick={() => onChange(EMPTY_SEARCH)}>Clear</button>}
      </div>
      {more && (
        <div className="search-more">
          <label className="filter"><span>From</span><input type="date" value={search.from} onChange={(e) => set("from", e.target.value)} /></label>
          <label className="filter"><span>To</span><input type="date" value={search.to} onChange={(e) => set("to", e.target.value)} /></label>
          <label className="filter" title="Runs whose best valid lap is at least this quick, e.g. 1:32.5">
            <span>Best lap</span>
            <input type="text" placeholder="under, e.g. 1:32.5" value={search.bestUnder} aria-invalid={!!bestBad}
              onChange={(e) => set("bestUnder", e.target.value)} />
          </label>
        </div>
      )}
    </div>
  );
}
