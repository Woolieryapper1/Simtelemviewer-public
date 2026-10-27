import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { applyLapColours, carSlot, freeSlot, loadColours, saveColours } from "./colours.js";
import ComparedLaps from "./ComparedLaps.jsx";
import { loadSettings } from "./Definitions.jsx";
import { lapKey } from "./LapsBox.jsx";
import MetricsPage from "./MetricsPage.jsx";
import RunDrawer from "./RunDrawer.jsx";
import SetupsPage from "./SetupsPage.jsx";
import StintPage from "./StintPage.jsx";
import TelemetryPage, { DEFAULT_LAYOUT } from "./TelemetryPage.jsx";
import TrackPage from "./TrackPage.jsx";
import { useTheme } from "./theme.js";
import { IconButton, Tabs, ThemeMenu, ToastProvider } from "./ui/index.js";

const PAGES = ["Telemetry", "Stint", "Metrics", "Track", "Setups"];

// The whole app: top bar, the collapsible run drawer, and the current page.
// State that several pages share (the run list, the open run, the compared
// laps, the metric definitions...) lives here and gets passed down to whatever
// needs it. Keeping it here also means it survives switching pages.
export default function App() {
  const [page, setPage] = useState("Telemetry");
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [sessions, setSessions] = useState([]);
  const [openId, setOpenId] = useState(null);
  const [notes, setNotes] = useState({}); // the team's notes, tags, stars and lap flags, by run key (see notes.py)
  const [error, setError] = useState(null);
  // Compared laps, first = reference. Each keeps its colour slot while others come and go.
  const [selected, setSelected] = useState([]);
  const [sectors, setSectors] = useState(25); // mini-sectors for the comparison
  const [comparison, setComparison] = useState(null);
  const [compareError, setCompareError] = useState(null);
  // Metric definitions for the Stint and Metrics pages, remembered in this browser
  const [settings, setSettings] = useState(loadSettings);
  // Telemetry chart rows (null until the default layout loads)
  const [panels, setPanels] = useState(null);
  const [layoutName, setLayoutName] = useState(DEFAULT_LAYOUT); // "" = edited, not saved
  // Trace colours picked in the telemetry readout, remembered in this browser (see colours.js)
  const [colours, setColours] = useState(loadColours);
  // A short text that changes whenever a colour does, so charts know to redraw
  const colourKey = JSON.stringify(colours);
  // Car colours are shades of the theme's palette, so they're worked out again when the theme changes.
  // A layout effect, so the colours are in place before the charts draw (they draw in plain effects).
  const { resolved: theme } = useTheme();
  useLayoutEffect(() => applyLapColours(colours, selected), [colourKey, selected, theme]);

  // Load the run list and the notes once when the app starts
  const loadNotes = () => api.notes().then(setNotes).catch((e) => setError(e.message));
  const loadSessions = () => api.sessions().then(setSessions).catch((e) => setError(e.message));
  const [team, setTeam] = useState(null); // the team library (see shared.py), if one is set
  useEffect(() => {
    loadSessions();
    loadNotes();
    api.team().then(setTeam).catch(() => {});
  }, []);

  // Save a change to a run's notes (or one lap's), then keep the server's copy.
  // Edits go one after another, so a quick second edit can't be overtaken by the first's answer.
  const noteQueue = useRef(Promise.resolve());
  function editNotes(session, body, lap = null) {
    const send = async () => {
      const saved = lap == null ? await api.editNotes(session.id, body) : await api.editLapNotes(session.id, lap, body);
      setNotes((prev) => ({ ...prev, [session.key]: saved }));
    };
    const done = noteQueue.current.then(send);
    noteQueue.current = done.catch(() => {}); // one failed edit doesn't block the next
    return done;
  }

  // Ask the backend to compare the ticked laps (sector times, mini-sectors,
  // racing lines...) whenever they change. Only full timed laps can be compared.
  const timedKeys = selected.filter((l) => l.kind === "timed").map((l) => l.key).join(",");
  useEffect(() => {
    const timed = selected.filter((l) => l.kind === "timed");
    setComparison(null);
    if (!timed.length) { setCompareError(null); return; }
    let stale = false;
    setCompareError(null);
    api.compare(timed, sectors)
      .then((c) => !stale && setComparison(c))
      .catch((e) => !stale && setCompareError(e.message));
    return () => { stale = true; };
  }, [timedKeys, sectors]); // re-run when which laps (or their order) or the sector count changes

  function keepColours(next) {
    setColours(next);
    saveColours(next);
  }
  // kind "laps" (key = colour slot), "car" (key = car name: that car's palette colour)
  // or "channels" (key = channel name, see colours.js); hex null = back to default
  function changeColour(kind, key, hex) {
    if (kind === "car") [kind, key] = ["laps", carSlot(colours, selected, key)];
    const group = { ...colours[kind] };
    if (hex) group[key] = hex; else delete group[key];
    keepColours({ ...colours, [kind]: group });
  }
  // A new colour order: the compared laps take its colours from the start again
  function changeColourOrder(order) {
    keepColours({ ...colours, order });
    setSelected((prev) => prev.map((lap, i) => ({ ...lap, slot: order[i] })));
  }

  function changeSettings(next) {
    setSettings(next);
    localStorage.setItem("metricSettings", JSON.stringify(next));
  }

  function toggleLap(session, lap) {
    const key = lapKey(session.id, lap.number);
    setSelected((prev) => {
      if (prev.some((x) => x.key === key)) return prev.filter((x) => x.key !== key);
      if (prev.length >= 6) return prev; // the drawer also stops a 7th tick
      const slot = freeSlot(colours, prev); // first free colour, in the chosen order
      return [...prev, {
        key, slot, sessionId: session.id, lap: lap.number, kind: lap.kind, invalid: lap.invalid,
        lapTime: lap.lap_time_s, driver: session.driver, car: session.car, started: session.started,
      }];
    });
  }
  const makeReference = (key) => setSelected((prev) => [prev.find((x) => x.key === key), ...prev.filter((x) => x.key !== key)]);
  const removeLap = (key) => setSelected((prev) => prev.filter((x) => x.key !== key));

  const open = sessions.find((s) => s.id === openId);

  return (
    <ToastProvider>
    <div className="app">
      <header className="topbar">
        <IconButton icon="menu" label="Toggle run list" onClick={() => setDrawerOpen(!drawerOpen)} />
        <span className="brand"><span className="brand-mark">◆</span>FS2627</span>
        <Tabs items={PAGES} value={page} onChange={setPage} label="Pages" />
        <span className="topbar-end">
          {open && <span className="topbar-right mono">{open.car} · {open.track}</span>}
          <ThemeMenu />
        </span>
      </header>

      <div className="body">
        {/* Hidden rather than removed, so the filters are still set when it opens again */}
        <RunDrawer hidden={!drawerOpen} sessions={sessions} team={team} onShared={loadSessions} openId={openId} onOpen={setOpenId}
          selected={selected} onToggleLap={toggleLap} notes={notes} onEditNotes={editNotes} onNotesImported={loadNotes} />
        <main className="page">
          {error && <div className="error">Couldn't load runs: {error}</div>}
          {page !== "Setups" && (
            <ComparedLaps laps={selected} comparison={comparison} error={compareError}
              onReference={makeReference} onRemove={removeLap}
              colours={colours} onColourOrder={changeColourOrder} onByCar={(byCar) => keepColours({ ...colours, byCar })} />
          )}
          {page === "Telemetry" && (
            <TelemetryPage laps={selected} comparison={comparison} rows={{ panels, setPanels, layoutName, setLayoutName }}
              colours={colours} colourKey={colourKey} onColour={changeColour} />
          )}
          {page === "Stint" && (
            <StintPage session={open} note={open && notes[open.key]} settings={settings} onSettings={changeSettings}
              selected={selected} onToggleLap={toggleLap} />
          )}
          {page === "Metrics" && (
            <MetricsPage laps={selected} settings={settings} onSettings={changeSettings} comparison={comparison} />
          )}
          {page === "Track" && <TrackPage laps={selected} comparison={comparison} sectors={sectors} onSectors={setSectors} colourKey={colourKey} />}
          {page === "Setups" && <SetupsPage />}
        </main>
      </div>
    </div>
    </ToastProvider>
  );
}
