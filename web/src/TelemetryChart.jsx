import { useEffect, useMemo, useRef, useState } from "react";
import { css, lapColor, LAP_DASHES, nearestIndex, PLOT_CONFIG, Plotly, rowTitle, timeDelta } from "./chartUtils.js";
import { useTheme } from "./theme.js";

// Size of each stacked row, the label strip above it, and the axis at the bottom (px)
const ROW_PX = 120, HEAD_PX = 26, BOTTOM_PX = 30;
// The readout panel down the left (values at the cursor), and the y-axis numbers next to it
const READOUT_PX = 236, TICKS_PX = 46;
// Height of one block in the readout; a row grows taller than ROW_PX if its blocks need it
const ENTRY_PX = 40;
const STEP_CHANNELS = new Set(["Gear"]); // drawn as steps, not slopes
// Channel colours for rows that overlay several channels (lap is then shown by line style).
// Throttle and brake always get the usual motorsport green and red; everything
// else takes the next colour from a palette that leaves green and red out.
// The colours are theme variables (tokens.css: --throttle, --brake, --channel-1..8).
const FIXED_COLORS = { "Throttle Pos": "--throttle", "Brake Pos": "--brake" };
const CHANNEL_COUNT = 8;
function channelColors(channels, picked = {}) {
  let next = 0;
  // A colour picked in the readout wins; otherwise the fixed or next palette colour
  return channels.map((ch) => picked[ch] ?? css(FIXED_COLORS[ch] ?? `--channel-${(next++ % CHANNEL_COUNT) + 1}`));
}

// "#6366f1" for the colour picker, whatever form the colour came in
function toHex(color) {
  if (/^#[0-9a-f]{6}$/i.test(color)) return color;
  const m = color.match(/\d+/g);
  return m ? "#" + m.slice(0, 3).map((v) => Number(v).toString(16).padStart(2, "0")).join("") : "#888888";
}

// Whole numbers (gear, 100 % throttle) without decimals; otherwise fewer decimals
// for bigger numbers, so the readout stays compact
function fmtValue(v) {
  if (v == null || !Number.isFinite(v)) return "–";
  if (Number.isInteger(v)) return String(v);
  const a = Math.abs(v);
  return v.toFixed(a >= 1000 ? 0 : a >= 100 ? 1 : a >= 10 ? 2 : 3);
}

// "Scale each channel" (autoscale) per overlay row, remembered in this browser.
// Keyed by the row's channels, so the setting follows that combination of channels.
const SCALE_KEY = "rowAutoscale";
const rowKey = (channels) => channels.join("|");
function loadScaled() {
  try { return JSON.parse(localStorage.getItem(SCALE_KEY) ?? "{}"); } catch { return {}; }
}

// Lowest and highest value of a channel over all the compared laps (so every lap
// shares the same scale and they stay comparable)
function channelRange(laps, ch) {
  let lo = Infinity, hi = -Infinity;
  for (const lap of laps) for (const v of lap.data[ch] ?? []) if (Number.isFinite(v)) { if (v < lo) lo = v; if (v > hi) hi = v; }
  return lo <= hi ? [lo, hi] : null;
}

// How many blocks a row's readout has: one per lap, or one per channel per lap for overlays
const entries = (row, laps) => (row.channels.length > 1 ? row.channels.length : 1) * laps.length;
// The same dash patterns as LAP_DASHES, written the SVG way for the little key
const SVG_DASHES = ["", "6 3", "2 3", "6 3 2 3", "10 4", "10 4 2 4"];

// All rows are ONE Plotly chart, so they share the distance axis and one cursor
// line. Row labels are HTML laid over the strips between rows: they're where you
// remove, reorder and drop channels.
//
// Instead of a pop-up following the mouse, a READOUT PANEL down the left shows
// every trace's value at the cursor, one box per row, lined up with the row.
// It's interactive: click a colour to change it, drag an entry onto another row
// to overlay it there, × to take a channel out of an overlay.
//   laps:   compared laps with .data / .units loaded, first = reference
//   panels: [[channel, ...], ...] - one row each; a row with several channels overlays them
//   known:  channels the reference run logged (null = not loaded yet)
//   drag:   "channel" / "row" while something is being dragged, else null
//   zoomTo: { range: [from, to] | null } - zoom the distance axis there (null = whole lap)
//   dragMode: "zoom" or "pan" - what dragging across the chart does
//   onHover(distance | null): the mouse moved along the chart (for the map's cursor dot)
//   colours / onColour(kind, key, hex): colours picked in the readout (see colours.js)
export default function TelemetryChart({ laps, panels, known, drag, zoomTo, dragMode, onHover, onRemoveRow, onRemoveChannel, onDragRow, onDragChannel, onDrop, colours = { laps: {}, channels: {} }, colourKey, onColour }) {
  const plotRef = useRef(null);
  const { resolved: theme } = useTheme(); // the chart takes its colours from the theme, so redraw when it changes
  const [cursor, setCursor] = useState(null); // distance (m) the readout shows; stays put when the mouse leaves
  const [, setView] = useState(0); // bumped on zoom/resize so the cursor line moves with the axis
  const [scaled, setScaled] = useState(loadScaled); // { "Steering Angle|Balance Slip Angle": true, ... }
  const isScaled = (row) => row.channels.length > 1 && !!scaled[rowKey(row.channels)];
  const toggleScaled = (row) => {
    const next = { ...scaled, [rowKey(row.channels)]: !isScaled(row) };
    if (!next[rowKey(row.channels)]) delete next[rowKey(row.channels)];
    setScaled(next);
    localStorage.setItem(SCALE_KEY, JSON.stringify(next));
  };
  const scaledKey = JSON.stringify(scaled);
  // The latest onHover, for the listener that's attached once below
  const hoverRef = useRef(onHover);
  hoverRef.current = onHover;

  const units = Object.assign({}, ...laps.map((l) => l.units));
  const rows = [];
  if (laps.length > 1) rows.push({ title: "Delta to ref  s", delta: true, channels: [] });
  panels.forEach((channels, panel) => rows.push({ title: rowTitle(channels, units), channels, panel }));

  // Each row is at least ROW_PX tall, taller if its readout blocks need the room
  const rowPx = rows.map((row) => Math.max(ROW_PX, entries(row, laps) * ENTRY_PX + 10));
  const plotPx = rowPx.reduce((a, b) => a + b, 0) + (rows.length - 1) * HEAD_PX;
  const height = HEAD_PX + plotPx + BOTTOM_PX;
  // where row i's label strip starts, from the top of the chart
  const rowTop = (i) => rowPx.slice(0, i).reduce((a, b) => a + b, 0) + i * HEAD_PX;

  useEffect(() => {
    const plot = plotRef.current;
    if (!rows.length || !laps.length) { Plotly.purge(plot); delete plot.dataset.listening; return; }
    const ref = laps[0];
    const grid = css("--border"), text = css("--text-3");

    const layout = {
      height, margin: { l: READOUT_PX + TICKS_PX, r: 10, t: HEAD_PX, b: BOTTOM_PX },
      uirevision: "keep", // keep the zoom when rows are added or removed
      dragmode: dragMode,
      paper_bgcolor: css("--card"), plot_bgcolor: css("--card"),
      font: { family: css("--mono"), size: 10, color: text },
      // Hover events still fire (they move the readout and the cursor line) but Plotly's own pop-up is off
      showlegend: false, hovermode: "x", hoverdistance: -1,
      xaxis: {
        anchor: `y${rows.length === 1 ? "" : rows.length}`, // ticks under the bottom row only
        gridcolor: grid, linecolor: grid, zeroline: false, ticksuffix: " m",
      },
    };
    const traces = [];
    rows.forEach((row, i) => {
      const axis = i === 0 ? "" : String(i + 1);
      // row i's plot starts rowTop(i) px below the top of the plotting area
      const top = 1 - rowTop(i) / plotPx;
      const autoscale = isScaled(row);
      layout[`yaxis${axis}`] = {
        domain: [Math.max(0, top - rowPx[i] / plotPx), top], gridcolor: grid, linecolor: grid, nticks: 4, fixedrange: true, // zoom only along distance
        zeroline: !!row.delta, zerolinecolor: css("--border-strong"),
        // Scaled rows have no single scale, so no numbers on the axis (the readout has the real values)
        ...(autoscale ? { range: [-0.06, 1.06], autorange: false, showticklabels: false } : { autorange: true }),
      };
      const line = (color, dash, name) => ({ color, width: 1.3, dash, shape: STEP_CHANNELS.has(name) ? "hv" : "linear" });
      const name = (lap) => `${lap.driver.slice(0, 12)} L${lap.lap}`;
      if (row.delta || row.channels.length === 1) {
        // One channel: colour = lap
        laps.forEach((lap) => {
          const y = row.delta ? timeDelta(lap.data, ref.data) : lap.data[row.channels[0]];
          if (!y) return; // channel not logged in this run
          traces.push({
            type: "scatter", mode: "lines", x: lap.data.Distance, y, yaxis: `y${axis}`,
            line: line(lapColor(lap.slot), "solid", row.channels[0]), hoverinfo: "none",
          });
        });
      } else {
        // Several channels: colour = channel, line style = lap
        // With "scale each channel" on, every channel is stretched to fill the row
        // (0 = its lowest value over the compared laps, 1 = its highest)
        const colors = channelColors(row.channels, colours.channels);
        row.channels.forEach((ch, c) => {
          const range = autoscale ? channelRange(laps, ch) : null;
          const fit = (values) => (range ? values.map((v) => (v - range[0]) / (range[1] - range[0] || 1)) : values);
          laps.forEach((lap, j) => lap.data[ch] && traces.push({
            type: "scatter", mode: "lines", x: lap.data.Distance, y: fit(lap.data[ch]), yaxis: `y${axis}`,
            line: line(colors[c], LAP_DASHES[j % LAP_DASHES.length], ch), hoverinfo: "none",
          }));
        });
      }
    });
    // Our own Zoom/Pan/Reset buttons replace Plotly's (which only show on hover);
    // the scroll wheel zooms along the distance
    Plotly.react(plot, traces, layout, { ...PLOT_CONFIG, displayModeBar: false, scrollZoom: true });
    // Hover listeners: attached once, Plotly keeps them across redraws
    if (!plot.dataset.listening) {
      plot.dataset.listening = "1";
      plot.on("plotly_hover", (e) => { setCursor(e.points[0].x); hoverRef.current?.(e.points[0].x); });
      plot.on("plotly_unhover", () => hoverRef.current?.(null)); // the readout keeps the last values
      plot.on("plotly_relayout", () => setView((v) => v + 1)); // zoomed: move the cursor line with it
      plot.on("plotly_afterplot", () => setView((v) => v + 1));
    }
    // Only redraw when the data, rows, drag mode or colours change - not on every
    // little change elsewhere in the app (that made everything slow)
  }, [laps, panels, dragMode, colourKey, scaledKey, theme]);

  // Delta to the reference lap, worked out once per set of laps (not on every mouse move)
  const deltas = useMemo(() => (laps.length > 1 ? laps.map((lap) => timeDelta(lap.data, laps[0].data)) : []), [laps]);

  // Zoom when asked to (a mini-sector clicked on the map, or Esc)
  useEffect(() => {
    const plot = plotRef.current;
    if (!zoomTo || !plot?.data) return;
    Plotly.relayout(plot, zoomTo.range ? { "xaxis.range": zoomTo.range } : { "xaxis.autorange": true });
  }, [zoomTo]);

  // Free Plotly's memory when the chart goes away
  useEffect(() => () => plotRef.current && Plotly.purge(plotRef.current), []);

  if (!rows.length) return <div className="empty">No rows yet. Add channels with "+ Channels".</div>;
  const missing = (ch) => ch != null && known && !known.has(ch); // the delta row has no channel

  // Value of one lap's channel at the cursor (laps are all on the same distance grid)
  const valueAt = (lap, values) => {
    if (cursor == null || !values || !lap.data?.Distance?.length) return null;
    return values[nearestIndex(lap.data.Distance, cursor)];
  };
  // Where the cursor line goes, in pixels from the left of the chart
  const full = plotRef.current?._fullLayout;
  const cursorPx = cursor != null && full?.xaxis?.l2p ? full._size.l + full.xaxis.l2p(cursor) : null;
  const inView = cursorPx != null && cursorPx >= full._size.l && cursorPx <= full._size.l + full._size.w;

  return (
    <div className="card telemetry" style={{ height }} onMouseLeave={() => onHover?.(null)}>
      <div ref={plotRef} />
      {rows.map((row, i) => {
        return (
          <div key={i} className={`row-label${i ? " sep" : ""}`} style={{ top: rowTop(i), height: HEAD_PX }}>
            {!row.delta && (
              <span className="row-handle" draggable title="Drag to move this row"
                onDragStart={(e) => onDragRow(e, row.panel)}>⋮⋮</span>
            )}
            {/* Every row shows its title; the channels themselves are in the readout on the left,
                where they can be recoloured, dragged to another row, or taken out */}
            <span className={`row-title${missing(row.channels[0]) && row.channels.length === 1 ? " missing" : ""}${row.channels.length === 1 ? " draggable" : ""}`}
              draggable={row.channels.length === 1} onDragStart={(e) => onDragChannel(e, row.channels[0])}
              title={row.delta ? "" : row.channels.length === 1 ? "Drag onto another row to overlay it" : row.channels.join(" · ")}>
              {row.title}
            </span>
            {isScaled(row) && <span className="muted">each channel scaled to fit</span>}
            <span className="row-right">
              {row.channels.length > 1 && laps.length > 1 && laps.map((lap, j) => (
                <span key={lap.key} className="row-key">
                  <svg width="18" height="4" aria-hidden="true">
                    <line x1="0" y1="2" x2="18" y2="2" stroke="currentColor" strokeWidth="1.5"
                      strokeDasharray={SVG_DASHES[j % SVG_DASHES.length]} />
                  </svg>
                  L{lap.lap} {lap.driver.slice(0, 10)}
                </span>
              ))}
              {row.channels.length > 1 && (
                <button type="button" className={`row-scale${isScaled(row) ? " on" : ""}`} aria-pressed={isScaled(row)}
                  title={isScaled(row) ? "Scaling each channel to fill the row - click to put them back on one scale"
                    : "Scale each channel to fill the row (so small ones like balance show next to big ones like steering)"}
                  onClick={() => toggleScaled(row)}>⇕</button>
              )}
              {!row.delta && (
                <button type="button" className="row-remove" aria-label="Remove this row"
                  onClick={() => onRemoveRow(row.panel)}>×</button>
              )}
            </span>
          </div>
        );
      })}
      {/* Cursor line through every row */}
      {inView && <div className="cursor-line" style={{ left: cursorPx, top: HEAD_PX, height: plotPx }} />}
      {/* Readout panel: one box per row, lined up with it */}
      {rows.map((row, i) => (
        <Readout key={`ro${i}`} row={row} laps={laps} top={rowTop(i) + HEAD_PX} height={rowPx[i]} units={units} scaled={isScaled(row)}
          colours={colours} onColour={onColour} valueAt={valueAt} deltas={deltas}
          missing={missing} onDragChannel={onDragChannel} onRemoveChannel={onRemoveChannel} />
      ))}
      {cursor != null && <div className="readout-distance mono" style={{ top: height - BOTTOM_PX, height: BOTTOM_PX, width: READOUT_PX }}>cursor {cursor.toFixed(0)} m</div>}
      {/* While dragging, each whole row (label + plot) becomes a drop target */}
      {drag && rows.map((row, i) => !row.delta && (
        <div key={`drop${i}`} className="row-drop" style={{ top: rowTop(i), height: rowPx[i] + HEAD_PX }}
          onDragOver={(e) => { e.preventDefault(); e.currentTarget.classList.add("over"); }}
          onDragLeave={(e) => e.currentTarget.classList.remove("over")}
          onDrop={(e) => onDrop(e, row.panel)}>
          <span>{drag === "channel" ? "Overlay here" : "Move row here"}</span>
        </div>
      ))}
    </div>
  );
}

// One row's box in the readout panel: a stacked block per trace with its name,
// its value at the cursor (big) and its colour in a box on the right.
//   single channel (or the delta row): a block per lap, coloured by lap -
//     changing the colour recolours that lap everywhere
//   overlay row: a block per channel (per lap, if several laps), grouped by lap
//     in compared-lap order, coloured by channel; the little line next to the
//     name shows the lap's line style
// Drag a block onto another row to overlay that channel there; × (overlays
// only) takes the channel out of this row.
function Readout({ row, laps, top, height, units, scaled, colours, onColour, valueAt, deltas, missing, onDragChannel, onRemoveChannel }) {
  const overlay = row.channels.length > 1;
  const colors = overlay ? channelColors(row.channels, colours.channels) : [];
  const blocks = [];
  if (!overlay) {
    const ch = row.channels[0];
    laps.forEach((lap, j) => blocks.push({
      key: lap.key, channel: ch, name: `L${lap.lap} ${lap.driver.slice(0, 14)}`,
      value: row.delta ? valueAt(lap, deltas[j]) : valueAt(lap, lap.data[ch]),
      unit: row.delta ? "s" : units[ch] ?? "", signed: row.delta,
      color: lapColor(lap.slot), pick: (hex) => onColour?.("laps", lap.slot, hex), pickTitle: "Change this lap's colour (everywhere)",
    }));
  } else {
    laps.forEach((lap, j) => row.channels.forEach((ch, c) => blocks.push({
      key: `${ch}${lap.key}`, channel: ch, name: laps.length > 1 ? `${ch} · L${lap.lap}` : ch, dash: laps.length > 1 ? j : null,
      value: valueAt(lap, lap.data[ch]), unit: units[ch] ?? "", missing: missing(ch), removable: true,
      range: scaled ? channelRange(laps, ch) : null,
      color: colors[c], pick: (hex) => onColour?.("channels", ch, hex), pickTitle: `Change ${ch}'s colour`,
    })));
  }
  return (
    <div className="readout" style={{ top, height, width: READOUT_PX }}>
      {blocks.map((b) => (
        <div key={b.key} className={`ro-block${b.missing ? " missing" : ""}`}
          draggable={!row.delta} onDragStart={(e) => !row.delta && onDragChannel(e, b.channel)}
          title={(b.range ? `Scaled: ${fmtValue(b.range[0])} to ${fmtValue(b.range[1])} ${b.unit} fills the row. ` : "")
            + (row.delta ? "" : `Drag onto another row to overlay ${b.channel} there`)}>
          <div className="ro-text">
            <div className="ro-name">
              {b.dash != null && (
                <svg width="16" height="4" aria-hidden="true">
                  <line x1="0" y1="2" x2="16" y2="2" stroke="currentColor" strokeWidth="1.5" strokeDasharray={SVG_DASHES[b.dash % SVG_DASHES.length]} />
                </svg>
              )}
              <span>{b.name}</span>
            </div>
            <div className="ro-value mono">
              {b.signed && b.value != null && b.value > 0 ? "+" : ""}{fmtValue(b.value)}
              {b.unit && <span className="ro-unit">{b.unit}</span>}
            </div>
          </div>
          {b.removable && (
            <button type="button" className="ro-remove" aria-label={`Take ${b.channel} out of this row`}
              onClick={() => onRemoveChannel(row.panel, b.channel)}>×</button>
          )}
          <label className="ro-colour" title={b.pickTitle} style={{ background: b.color }}>
            <input type="color" value={toHex(b.color)} onChange={(e) => b.pick(e.target.value)} />
          </label>
        </div>
      ))}
    </div>
  );
}
