import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import ChannelPicker from "./ChannelPicker.jsx";
import { drawMap, moveMapCursor, resetMapZoom, sectorAt, zoomMapTo } from "./charts.js";
import Plot from "./Plot.jsx";
import TelemetryChart from "./TelemetryChart.jsx";
import { comparedLaps } from "./TrackPage.jsx";
import { Button, Field, Input, Segmented, Select, useToast } from "./ui/index.js";
import useLapData from "./useLapData.js";

export const DEFAULT_LAYOUT = "Driver inputs";
const MAX_ROWS = 12, MAX_PER_ROW = 8; // same limits as backend/app/layouts.py

// Telemetry page: the rows of the chart (which channels, in which row) can be
// edited here and saved as a layout the whole team can load. Above it, a small
// track map follows the mouse; click a mini-sector on it to zoom the traces.
// `rows` ({ panels, setPanels, layoutName, setLayoutName }) lives in App, so
// edits survive switching to another page and back.
export default function TelemetryPage({ laps, comparison, rows: { panels: savedPanels, setPanels, layoutName, setLayoutName }, colours, colourKey, onColour }) {
  const panels = savedPanels ?? []; // null until the default layout has loaded
  const [layouts, setLayouts] = useState([]);
  const [channels, setChannels] = useState([]); // [{name, unit, maths, approximate, description}]
  const [drag, setDrag] = useState(null); // what's being dragged: "channel", "row" or null
  const [saving, setSaving] = useState(null); // name typed into "Save as", null = box closed
  const [message, setMessage] = useState(null);
  const toast = useToast();
  const [zoomTo, setZoomTo] = useState(null); // { range } to zoom the traces to
  const [mapOpen, setMapOpen] = useState(true);
  const mapRef = useRef(null);

  // Load the saved layouts; the very first time, show the default one
  const loadLayouts = () => api.layouts().then((all) => { setLayouts(all); return all; });
  useEffect(() => {
    loadLayouts().then((all) => {
      if (savedPanels == null) setPanels(all.find((l) => l.name === DEFAULT_LAYOUT)?.panels ?? []);
    }).catch((e) => setMessage(e.message));
  }, []);

  // Reset zoom (button or Esc): traces and map back to the whole lap
  const [dragMode, setDragMode] = useState("zoom");
  function resetZoom() { setZoomTo({ range: null }); resetMapZoom(mapRef.current); }
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") resetZoom(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  function zoomToSector(s) {
    const pad = (s.end_m - s.start_m) * 0.15;
    setZoomTo({ range: [s.start_m - pad, s.end_m + pad] });
  }

  // Channel list of the reference lap's run (fetched again if that changes)
  const refSession = laps[0]?.sessionId;
  useEffect(() => {
    if (refSession) api.channels(refSession).then(setChannels).catch(() => {});
  }, [refSession]);

  // Only ask for channels this run actually logged; others show as missing
  const known = new Set(channels.map((c) => c.name));
  const wanted = panels.flat().filter((c) => !channels.length || known.has(c));
  const { cache, loading, error } = useLapData(laps, wanted);
  // A lap is drawn once its data has arrived (Distance/Time always come with it).
  // useMemo keeps the same list between renders unless something it uses
  // changed, so the chart knows it doesn't need redrawing.
  const ready = useMemo(() => laps
    .filter((l) => cache[l.key] && wanted.every((c) => cache[l.key].data[c]))
    .map((l) => ({ ...l, ...cache[l.key] })), [laps, cache, wanted.join(",")]);

  // Move the map's cursor dot at most every 30 ms, not on every mouse event:
  // remember the latest position and draw it when the timer fires
  const cursor = useRef({ timer: null, distance: null });
  function hover(distance) {
    if (!comparison) return;
    cursor.current.distance = distance;
    if (cursor.current.timer) return; // already scheduled; it'll use the latest position
    cursor.current.timer = setTimeout(() => {
      cursor.current.timer = null;
      moveMapCursor(mapRef.current, comparison, cursor.current.distance);
    }, 30);
  }

  // ---- editing the rows; every change makes the layout "unsaved" ----
  function change(next) {
    setPanels(next.filter((row) => row.length).slice(0, MAX_ROWS)); // empty rows disappear
    setLayoutName("");
  }
  const removeEverywhere = (name) => panels.map((row) => row.filter((n) => n !== name));
  const toggleChannel = (name) =>
    change(panels.flat().includes(name) ? removeEverywhere(name) : [...panels, [name]]);
  // Move a channel into row `target`, or into a new row at the bottom if target is null
  function moveChannel(name, target) {
    if (target != null && !panels[target].includes(name) && panels[target].length >= MAX_PER_ROW) {
      setMessage(`A row can overlay at most ${MAX_PER_ROW} channels`);
      return;
    }
    const next = panels.map((row, i) => (i === target ? (row.includes(name) ? row : [...row, name]) : row.filter((n) => n !== name)));
    change(target == null ? [...next, [name]] : next);
  }
  function moveRow(from, to) {
    const next = [...panels];
    next.splice(to, 0, ...next.splice(from, 1));
    change(next);
  }

  // ---- drag and drop: channels carry "text/x-channel", rows carry "text/x-row" ----
  function startDrag(e, kind, value) {
    e.dataTransfer.setData(kind === "channel" ? "text/x-channel" : "text/x-row", String(value));
    // Show the drop zones a moment later: changing the page during the very
    // first instant of a drag makes the browser cancel it
    setTimeout(() => setDrag(kind), 0);
  }
  useEffect(() => {
    const end = () => setDrag(null);
    window.addEventListener("dragend", end);
    window.addEventListener("drop", end);
    return () => { window.removeEventListener("dragend", end); window.removeEventListener("drop", end); };
  }, []);
  function dropOn(e, target) { // target = row index, or null for "new row"
    e.preventDefault();
    const name = e.dataTransfer.getData("text/x-channel");
    const row = e.dataTransfer.getData("text/x-row");
    if (name) moveChannel(name, target);
    else if (row !== "" && target != null) moveRow(Number(row), target);
  }

  // ---- layouts ----
  function pickLayout(name) {
    setLayoutName(name);
    setPanels(layouts.find((l) => l.name === name)?.panels ?? panels);
  }
  async function saveLayout() {
    const name = saving.trim();
    if (!name) return setMessage("Enter a name for the layout");
    if (layouts.some((l) => l.name === name && l.built_in)) return setMessage("That's a built-in layout name. Try another.");
    try {
      await api.saveLayout(name, panels);
      await loadLayouts();
      setLayoutName(name);
      setSaving(null);
      toast(`Saved "${name}" for the team`, { tone: "good" });
    } catch (e) { setMessage(e.message); }
  }
  async function deleteLayout() {
    if (!window.confirm(`Delete the team layout "${layoutName}"?`)) return;
    try {
      await api.deleteLayout(layoutName);
      await loadLayouts();
      setLayoutName("");
      toast(`Deleted "${layoutName}"`);
    } catch (e) { setMessage(e.message); }
  }
  const current = layouts.find((l) => l.name === layoutName);

  return (
    <>
      <div className="toolbar">
        <Field label="Layout">
          <Select value={layoutName} onChange={(e) => pickLayout(e.target.value)}>
            <option value="">Custom (unsaved)</option>
            <optgroup label="Built-in">
              {layouts.filter((l) => l.built_in).map((l) => <option key={l.name}>{l.name}</option>)}
            </optgroup>
            <optgroup label="Team">
              {layouts.filter((l) => !l.built_in).map((l) => <option key={l.name}>{l.name}</option>)}
            </optgroup>
          </Select>
        </Field>
        {saving == null
          ? <Button onClick={() => setSaving("")}>Save as…</Button>
          : (
            <form className="save-as" onSubmit={(e) => { e.preventDefault(); saveLayout(); }}>
              <Input autoFocus placeholder="Layout name" aria-label="Layout name" value={saving} maxLength={60}
                onChange={(e) => { setSaving(e.target.value); setMessage(null); }} />
              <Button type="submit" variant="primary">Save</Button>
              <Button onClick={() => setSaving(null)}>Cancel</Button>
            </form>
          )}
        {current && !current.built_in && <Button variant="danger" icon="trash" onClick={deleteLayout}>Delete</Button>}
        <ChannelPicker channels={channels} used={new Set(panels.flat())} onToggle={toggleChannel}
          onDragStart={(e, name) => startDrag(e, "channel", name)} />
        {loading && <span className="muted">Loading…</span>}
        {(message || error) && <span className="muted" role="status">{message || error}</span>}
        <span className="push">
          {/* What dragging across the chart does; the scroll wheel always zooms */}
          <Segmented label="Drag mode" value={dragMode} onChange={setDragMode}
            options={[{ value: "zoom", label: "Zoom", icon: "zoom-in" }, { value: "pan", label: "Pan", icon: "move" }]} />
          <Button icon="reset" onClick={resetZoom} title="Esc">Reset zoom</Button>
        </span>
      </div>

      {comparison && laps.length > 0 && (
        <MiniMap open={mapOpen} onToggle={() => setMapOpen(!mapOpen)} mapRef={mapRef}
          comparison={comparison} laps={laps} onSector={zoomToSector} colourKey={colourKey} />
      )}

      {laps.length === 0
        ? <div className="empty">Tick laps in the drawer to see their telemetry</div>
        : (
          <TelemetryChart laps={ready} panels={panels} known={channels.length ? known : null} drag={drag}
            zoomTo={zoomTo} dragMode={dragMode} onHover={hover} colours={colours} colourKey={colourKey} onColour={onColour}
            onRemoveRow={(i) => change(panels.filter((_, k) => k !== i))}
            onRemoveChannel={(i, name) => change(panels.map((row, k) => (k === i ? row.filter((n) => n !== name) : row)))}
            onDragRow={(e, i) => startDrag(e, "row", i)}
            onDragChannel={(e, name) => startDrag(e, "channel", name)}
            onDrop={dropOn} />
        )}
      {/* While a channel is dragged: drop here to give it a row of its own */}
      {drag === "channel" && laps.length > 0 && (
        <div className="new-row" onDragOver={(e) => e.preventDefault()} onDrop={(e) => dropOn(e, null)}>
          Drop here for a new row
        </div>
      )}
    </>
  );
}

// Small track map: fastest lap per mini-sector (or speed, with one lap).
// Hovering the traces moves a dot round it; clicking a mini-sector zooms the traces.
function MiniMap({ open, onToggle, mapRef, comparison: cmp, laps, onSector, colourKey }) {
  const shown = comparedLaps(cmp, laps).map((l) => ({ ...l, shortLabel: `${l.driver.slice(0, 12)} L${l.lap}` }));
  const skipped = laps.length - shown.length;
  return (
    <div className="card minimap">
      <button type="button" className="defs-head" aria-expanded={open} onClick={onToggle}>
        <span>{open ? "▾" : "▸"} Track map</span>
        <span className="muted">
          {shown.length > 1 ? "coloured by the quickest lap through each mini-sector" : "coloured by speed"}
          {skipped > 0 && ` · ${skipped} out or unfinished lap${skipped > 1 ? "s" : ""} left out`}
          {" · click a mini-sector to zoom"}
        </span>
      </button>
      {open && (
        <Plot ref={mapRef} deps={[cmp, shown.map((l) => `${l.key}${l.slot}`).join(), colourKey]} draw={(el) => {
          drawMap(el, cmp, shown, { surface: true, lines: false, labels: false, height: 300, colouring: shown.length > 1 ? "dominance" : "speed" });
          el.removeAllListeners?.("plotly_click");
          el.on("plotly_click", (e) => {
            const s = sectorAt(cmp, e.points[0].x, e.points[0].y);
            if (s) { onSector(s); zoomMapTo(el, cmp, s); }
          });
        }} />
      )}
    </div>
  );
}
