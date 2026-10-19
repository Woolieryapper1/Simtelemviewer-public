import { useState } from "react";
import { fmtDay, fmtLap, fmtTime } from "./format.js";
import { carCategory } from "./carCategories.js";
import LapsBox from "./LapsBox.jsx";
import RunNotes from "./RunNotes.jsx";
import RunSearch, { EMPTY_SEARCH, isSearching, matchesSearch } from "./RunSearch.jsx";
import { Chip, Select } from "./ui/index.js";

const GAME_LABELS = { ac: "Assetto Corsa", iracing: "iRacing", csv: "CSV imports" };

const uniqueSorted = (list) => [...new Set(list)].sort();

// Browse game, track and car in order; keep compared laps across filter changes.
export default function RunDrawer({ hidden, sessions, openId, onOpen, selected, onToggleLap, notes, onEditNotes, onNotesImported }) {
  const [game, setGame] = useState("");
  const [category, setCategory] = useState("");
  const [track, setTrack] = useState("");
  const [cars, setCars] = useState([]);
  const [drivers, setDrivers] = useState([]);
  const [search, setSearch] = useState(EMPTY_SEARCH);
  const games = uniqueSorted(sessions.map((s) => s.source));
  const gameValue = games.length === 1 ? games[0] : game;
  const inGame = sessions.filter((s) => s.source === gameValue);
  const searching = isSearching(search);
  const pool = searching ? inGame.filter((s) => matchesSearch(s, notes[s.key], search)) : inGame;
  const tracks = uniqueSorted(pool.map((s) => s.track));
  const trackValue = tracks.length === 1 ? tracks[0] : tracks.includes(track) ? track : "";
  const onTrack = trackValue ? pool.filter((s) => s.track === trackValue) : [];
  const categories = uniqueSorted(onTrack.map((s) => carCategory(s.car)));
  const categoryValue = categories.includes(category) ? category : "";
  const carOptions = uniqueSorted(onTrack.filter((s) => !categoryValue || carCategory(s.car) === categoryValue).map((s) => s.car));
  const carsPicked = carOptions.length === 1 ? carOptions : cars.filter((c) => carOptions.includes(c));
  const inCars = onTrack.filter((s) => carsPicked.includes(s.car));
  const driverOptions = uniqueSorted(inCars.map((s) => s.driver));
  const driversPicked = drivers.filter((d) => driverOptions.includes(d));
  const driversOn = driversPicked.length ? driversPicked : driverOptions;
  const shown = inCars.filter((s) => driversOn.includes(s.driver));

  const chooseGame = (value) => { setGame(value); setTrack(""); setCategory(""); setCars([]); setDrivers([]); };
  const chooseTrack = (value) => { setTrack(value); setCategory(""); setCars([]); setDrivers([]); };
  const chooseCategory = (value) => { setCategory(value); setCars([]); setDrivers([]); };
  const addCar = (value) => { if (value) setCars((prev) => [...new Set([...prev, value])]); setDrivers([]); };
  const days = [];
  for (const s of shown) {
    const day = fmtDay(s.started);
    if (days.at(-1)?.day !== day) days.push({ day, runs: [] });
    days.at(-1).runs.push(s);
  }

  // What's still missing, so the empty box can say what to do next
  const hint = !sessions.length ? "No runs found. Check the telemetry folders in my-settings.bat."
    : !gameValue ? "Choose a game to browse its tracks."
    : !pool.length ? "No runs match the search in this game."
    : !trackValue ? "Choose a track to see its cars."
    : !carsPicked.length ? "Choose a car to see its runs."
    : !shown.length ? "No runs match these filters." : "";
  const open = sessions.find((s) => s.id === openId);

  return (
    <aside className="drawer" hidden={hidden}>
      <label className="filter">
        <span>Game</span>
        <Select className="block" value={gameValue} onChange={(e) => chooseGame(e.target.value)}>
          <option value="">Choose game…</option>
          {games.map((g) => <option key={g} value={g}>{GAME_LABELS[g] ?? g}</option>)}
        </Select>
      </label>
      <RunSearch search={search} onChange={setSearch} sessions={inGame} notes={notes} />
      <label className="filter">
        <span>Track</span>
        <Select className="block" value={trackValue} disabled={!gameValue} onChange={(e) => chooseTrack(e.target.value)}>
          <option value="">Choose track…</option>
          {tracks.map((t) => <option key={t} value={t}>{t}</option>)}
        </Select>
      </label>
      {trackValue && <>
        {categories.length > 1 && <label className="filter">
          <span>Class</span>
          <Select className="block" value={categoryValue} onChange={(e) => chooseCategory(e.target.value)}>
            <option value="">All classes</option>
            {categories.map((c) => <option key={c} value={c}>{c}</option>)}
          </Select>
        </label>}
        <label className="filter">
          <span>Car</span>
          <Select className="block" value={carOptions.length === 1 ? carOptions[0] : ""} onChange={(e) => addCar(e.target.value)}>
            <option value="">{carsPicked.length ? "Add another car…" : "Choose car…"}</option>
            {categories.filter((c) => !categoryValue || c === categoryValue).map((c) => (
              <optgroup key={c} label={c}>
                {carOptions.filter((car) => carCategory(car) === c).map((car) => (
                  <option key={car} value={car} disabled={carOptions.length > 1 && carsPicked.includes(car)}>{car}</option>
                ))}
              </optgroup>
            ))}
          </Select>
        </label>
        {carOptions.length > 1 && carsPicked.length > 0 && <Chips label="Selected" options={carsPicked} on={carsPicked} onChange={setCars} />}
      </>}
      {carsPicked.length > 0 && driverOptions.length > 1 && <label className="filter">
        <span>Driver</span>
        <Select className="block" value={driversPicked[0] ?? ""} onChange={(e) => setDrivers(e.target.value ? [e.target.value] : [])}>
          <option value="">All drivers</option>
          {driverOptions.map((d) => <option key={d} value={d}>{d}</option>)}
        </Select>
      </label>}

      <div className="box runs">
        {hint && <div className="box-empty">{hint}</div>}
        {days.map(({ day, runs }) => (
          <section key={day}>
            <h3 className="day">{day} <span>{runs.length} run{runs.length === 1 ? "" : "s"}</span></h3>
            {runs.map((s) => {
              const note = notes[s.key];
              const mixedTracks = !trackValue && tracks.length > 1;
              return (
                <button key={s.id} type="button" className="run" title={note?.notes ? `${s.id}
${note.notes}` : s.id}
                  aria-selected={s.id === openId} onClick={() => onOpen(s.id)}>
                  <span className="run-time mono">{note?.star && <span className="star on">★</span>}{fmtTime(s.started)}</span>
                  <span className="run-laps">{s.valid_laps}/{s.lap_count} valid</span>
                  <span className="run-best mono">{fmtLap(s.best_lap_s)}</span>
                  {/* Only say whose run / which car / which track when the list mixes them */}
                  {(driversOn.length > 1 || carsPicked.length > 1 || mixedTracks) && (
                    <span className="run-who">
                      {[mixedTracks && s.track, driversOn.length > 1 && s.driver, carsPicked.length > 1 && s.car].filter(Boolean).join(" · ")}
                    </span>
                  )}
                  {note?.tags.length > 0 && <span className="run-tags">{note.tags.map((t) => <span key={t} className="tag">{t}</span>)}</span>}
                </button>
              );
            })}
          </section>
        ))}
      </div>

      {open && <RunNotes key={open.key} session={open} note={notes[open.key]} notes={notes} onEdit={onEditNotes} onImported={onNotesImported} />}
      <LapsBox session={open} note={open && notes[open.key]} selected={selected} onToggle={onToggleLap}
        onEditLap={(lap, body) => onEditNotes(open, body, lap)} />
    </aside>
  );
}

// A row of toggle chips; any number can be on at once.
// The toggle works on the latest saved list (prev), so quick clicks don't undo each other.
function Chips({ label, options, on, onChange }) {
  const toggle = (o) => onChange((prev) => (prev.includes(o) ? prev.filter((x) => x !== o) : [...prev, o]));
  return (
    <div className="filter chips-filter">
      <span>{label}</span>
      <div className="chips">
        {options.map((o) => (
          <Chip key={o} title={o} pressed={on.includes(o)} onClick={() => toggle(o)}>{o}</Chip>
        ))}
      </div>
    </div>
  );
}
