import { useEffect, useMemo, useRef, useState } from "react";
import { css, lapColor, nearestIndex, PLOT_CONFIG, Plotly, rowTitle, timeDelta } from "./chartUtils.js";
import { mixColours, toHex } from "./colours.js";
import { describe, rowSeries, scaledUnit, SCALES, SMOOTHING } from "./rowMaths.js";
import { useTheme } from "./theme.js";

// Size of each stacked row, the label strip above it, and the axis at the bottom (px)
const ROW_PX = 120, HEAD_PX = 26, BOTTOM_PX = 30;
// The readout panel down the left (values at the cursor), and the y-axis numbers next to it
const READOUT_PX = 236, TICKS_PX = 46;
// Height of one block in the readout; a row grows taller than ROW_PX if its blocks need it
const ENTRY_PX = 40;
const STEP_CHANNELS = new Set(["Gear"]); // drawn as steps, not slopes
// Channel colours for rows that overlay several channels: every channel on every
// lap has its own colour, all solid lines (no dashes to decode).
// On the reference lap, throttle and brake get the usual motorsport green and red;
// everything else takes the next colour from a palette that leaves green and red
// out (theme variables in tokens.css: --throttle, --brake, --channel-1..8).
// The other laps start as the same colours faded towards the text colour (white
// on the dark theme), the 2nd lap most, so "my throttle green, his white" is the default.
// Any of them can be changed in the readout (see colours.js for how picks are kept).
const FIXED_COLORS = { "Throttle Pos": "--throttle", "Brake Pos": "--brake" };
const CHANNEL_COUNT = 8;
const FADES = [0, 0.7, 0.4, 0.85, 0.25, 0.55]; // by compared-lap position
function channelColors(channels, picked = {}, lap = 0) {
  let next = 0;
  return channels.map((ch) => {
    const base = picked[ch] ?? css(FIXED_COLORS[ch] ?? `--channel-${(next++ % CHANNEL_COUNT) + 1}`);
    return lap === 0 ? base : picked[`${ch}@${lap}`] ?? mixColours(base, css("--text"), FADES[lap % FADES.length]);
  });
}

// Whole numbers (gear, 100 % throttle) without decimals; otherwise fewer decimals
// for bigger numbers, so the readout stays compact
function fmtValue(v) {
  if (v == null || !Number.isFinite(v)) return "–";
  if (Number.isInteger(v)) return String(v);
  const a = Math.abs(v);
  return v.toFixed(a >= 1000 ? 0 : a >= 100 ? 1 : a >= 10 ? 2 : 3);
}

// Each row's scale and smoothing ({ scale, smooth }, see rowMaths.js), remembered
// in this browser. Keyed by the row's channels, so the setting follows that combination of channels.
const OPTS_KEY = "rowOptions";
const rowKey = (channels) => channels.join("|");
function loadOpts() {
  try {
    const saved = localStorage.getItem(OPTS_KEY);
    if (saved) return JSON.parse(saved);
    // The old "scale each channel" toggle, from before there were other options
    const old = JSON.parse(localStorage.getItem("rowAutoscale") ?? "{}");
    return Object.fromEntries(Object.keys(old).filter((k) => old[k]).map((k) => [k, { scale: "fit" }]));
  } catch { return {}; }
}

// How many blocks a row's readout has: one per lap, or one per channel per lap for overlays
const entries = (row, laps) => (row.channels.length > 1 ? row.channels.length : 1) * laps.length;

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
// Each row can be smoothed, and an overlay row's channels put on one scale in
// other ways (% of their total, ÷ their mean...): the two little menus in its header.
export default function TelemetryChart({ laps, panels, known, drag, zoomTo, dragMode, onHover, onRemoveRow, onRemoveChannel, onDragRow, onDragChannel, onDrop, colours = { laps: {}, channels: {} }, colourKey, onColour }) {
  const plotRef = useRef(null);
  const { resolved: theme } = useTheme(); // the chart takes its colours from the theme, so redraw when it changes
  const [cursor, setCursor] = useState(null); // distance (m) the readout shows; stays put when the mouse leaves
  const [, setView] = useState(0); // bumped on zoom/resize so the cursor line moves with the axis
  const [opts, setOpts] = useState(loadOpts); // { "Tire Load FL|Tire Load FR": { scale: "share", smooth: 30 }, ... }
  const optsOf = (row) => (row.delta ? {} : opts[rowKey(row.channels)] ?? {});
  const changeOpts = (row, change) => {
    const key = rowKey(row.channels);
    const mine = { ...opts[key], ...change };
    if (!mine.smooth) delete mine.smooth;
    if (!mine.scale || mine.scale === "same") delete mine.scale;
    const next = { ...opts, [key]: mine };
    if (!Object.keys(mine).length) delete next[key];
    setOpts(next);
    try { localStorage.setItem(OPTS_KEY, JSON.stringify(next)); } catch { /* storage off: just not remembered */ }
  };
  const optsKey = JSON.stringify(opts);
  // The latest onHover, for the listener that's attached once below
  const hoverRef = useRef(onHover);
  hoverRef.current = onHover;

  const units = Object.assign({}, ...laps.map((l) => l.units));
  const rows = [];
  if (laps.length > 1) rows.push({ title: "Delta to ref  s", delta: true, channels: [] });
  panels.forEach((channels, panel) => {
    // The title shows the unit of what's drawn: "%" once tyre loads are a share of their total
    const scale = channels.length > 1 ? opts[rowKey(channels)]?.scale : null;
    const shown = Object.fromEntries(channels.map((ch) => [ch, scaledUnit(scale, units[ch])]));
    rows.push({ title: rowTitle(channels, shown), channels, panel });
  });

  // Each row is at least ROW_PX tall, taller if its readout blocks need the room
  const rowPx = rows.map((row) => Math.max(ROW_PX, entries(row, laps) * ENTRY_PX + 10));
  const plotPx = rowPx.reduce((a, b) => a + b, 0) + (rows.length - 1) * HEAD_PX;
  const height = HEAD_PX + plotPx + BOTTOM_PX;
  // where row i's label strip starts, from the top of the chart
  const rowTop = (i) => rowPx.slice(0, i).reduce((a, b) => a + b, 0) + i * HEAD_PX;

  // Each row's channels for every lap, smoothed and scaled (null for the delta row).
  // Worked out once per change, not on every mouse move.
  const series = useMemo(() => rows.map((row) => (row.delta ? null : rowSeries(row.channels, laps, optsOf(row)))),
    [laps, panels, optsKey]);

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
      const s = series[i];
      layout[`yaxis${axis}`] = {
        domain: [Math.max(0, top - rowPx[i] / plotPx), top], gridcolor: grid, linecolor: grid, nticks: 4, fixedrange: true, // zoom only along distance
        zeroline: !!row.delta || s?.scale === "diff", zerolinecolor: css("--border-strong"),
        ticksuffix: s?.scale === "share" ? " %" : s?.scale === "mean" ? "×" : "",
        // "Each 0-1" rows have no single scale, so no numbers on the axis (the readout has the real values)
        ...(s?.scale === "fit" ? { range: [-0.06, 1.06], autorange: false, showticklabels: false } : { autorange: true }),
      };
      const line = (color, name) => ({ color, width: 1.3, shape: STEP_CHANNELS.has(name) ? "hv" : "linear" });
      if (row.delta || row.channels.length === 1) {
        // One channel: colour = lap
        laps.forEach((lap, j) => {
          const y = row.delta ? timeDelta(lap.data, ref.data) : s.plot[j][row.channels[0]];
          if (!y) return; // channel not logged in this run
          traces.push({
            type: "scatter", mode: "lines", x: lap.data.Distance, y, yaxis: `y${axis}`,
            line: line(lapColor(lap.slot), row.channels[0]), hoverinfo: "none",
          });
        });
      } else {
        // Several channels: a colour for every channel on every lap
        laps.forEach((lap, j) => {
          const colors = channelColors(row.channels, colours.channels, j);
          row.channels.forEach((ch, c) => s.plot[j][ch] && traces.push({
            type: "scatter", mode: "lines", x: lap.data.Distance, y: s.plot[j][ch], yaxis: `y${axis}`,
            line: line(colors[c], ch), hoverinfo: "none",
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
  }, [laps, series, dragMode, colourKey, theme]);

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
            {describe(optsOf(row), row.channels) && <span className="muted">{describe(optsOf(row), row.channels)}</span>}
            <span className="row-right">
              {row.channels.length > 1 && (
                <select className={`row-select${optsOf(row).scale ? " on" : ""}`} aria-label="Scale"
                  value={optsOf(row).scale ?? "same"} onChange={(e) => changeOpts(row, { scale: e.target.value })}
                  title={"How this row's channels are put on a scale. " + SCALES[optsOf(row).scale ?? "same"].hint}>
                  {Object.entries(SCALES).map(([k, v]) => <option key={k} value={k} title={v.hint}>{v.label}</option>)}
                </select>
              )}
              {!row.delta && (
                <select className={`row-select${optsOf(row).smooth ? " on" : ""}`} aria-label="Smoothing"
                  value={optsOf(row).smooth ?? 0} onChange={(e) => changeOpts(row, { smooth: Number(e.target.value) })}
                  title={"Smooth this row with a moving average this many metres wide, to calm noisy channels like tyre loads over kerbs."
                    + " The readout shows the smoothed values. The same distance covers more time at low speed, so slow corners get smoothed more"}>
                  {SMOOTHING.map((m) => <option key={m} value={m}>{m ? `Smooth ${m} m` : "No smoothing"}</option>)}
                </select>
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
        <Readout key={`ro${i}`} row={row} laps={laps} top={rowTop(i) + HEAD_PX} height={rowPx[i]} units={units} series={series[i]}
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
//     changing the colour recolours that lap everywhere (or that car, when
//     colouring by car)
//   overlay row: a block per channel (per lap, if several laps), grouped by lap
//     in compared-lap order, each with its own colour
// Drag a block onto another row to overlay that channel there; × (overlays
// only) takes the channel out of this row.
function Readout({ row, laps, top, height, units, series, colours, onColour, valueAt, deltas, missing, onDragChannel, onRemoveChannel }) {
  const blocks = [];
  if (row.delta || row.channels.length === 1) {
    const ch = row.channels[0];
    laps.forEach((lap, j) => blocks.push({
      key: lap.key, channel: ch, name: `L${lap.lap} ${lap.driver.slice(0, 14)}`,
      value: row.delta ? valueAt(lap, deltas[j]) : valueAt(lap, series.values[j][ch]),
      unit: row.delta ? "s" : units[ch] ?? "", signed: row.delta,
      color: lapColor(lap.slot),
      pick: (hex) => (colours.byCar ? onColour?.("car", lap.car, hex) : onColour?.("laps", lap.slot, hex)),
      pickTitle: colours.byCar ? `Change ${lap.car}'s colour (everywhere)` : "Change this lap's colour (everywhere)",
    }));
  } else {
    laps.forEach((lap, j) => {
      const colors = channelColors(row.channels, colours.channels, j);
      row.channels.forEach((ch, c) => blocks.push({
        key: `${ch}${lap.key}`, channel: ch, name: laps.length > 1 ? `${ch} · L${lap.lap} ${lap.driver.slice(0, 8)}` : ch,
        value: valueAt(lap, series.values[j][ch]), unit: series.unit(ch, units[ch]), missing: missing(ch), removable: true,
        signed: series.scale === "diff", range: series.ranges[ch],
        color: colors[c], pick: (hex) => onColour?.("channels", j ? `${ch}@${j}` : ch, hex),
        pickTitle: laps.length > 1 ? `Change ${ch}'s colour on L${lap.lap} ${lap.driver}` : `Change ${ch}'s colour`,
      }));
    });
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
