// Lap colours, and the trace colours the user picks in the telemetry readout.
//
// ---- The lap palette ----
// Each compared lap keeps a colour slot (1-6) while others come and go. The
// colours themselves are CSS variables (--lap-1 ... --lap-6) in ui/tokens.css,
// with a set for each theme, so charts, maps and tables all read the same ones.
// Tyre charts reuse slots 1-4 for FL, FR, RL, RR.
//
//   slot   dark       light      hue
//   1      #8a80f5    #4f46e5    indigo (the app's accent)
//   2      #16a374    #0f9a6a    aqua
//   3      #d95926    #e35f2a    orange
//   4      #2084b4    #0679a8    blue
//   5      #c98500    #c98500    amber
//   6      #d55181    #d6578a    pink
//
// Picked and checked with the colour-blind palette checks from our data-viz
// guide (OKLab colour distance, x100, with red-blind and green-blind vision simulated):
//   - neighbouring slots are at least 9.3 apart for colour-blind readers
//     (8 is the target) and 19 apart for everyone else (15 is the floor)
//   - the first FOUR slots are all that far from EACH OTHER (not just their
//     neighbours), so 2-4 laps or the four tyres never get two look-alikes
//   - every colour stands out from the card it's drawn on (3:1 or more), and
//     sits in a band of lightness that reads well on that theme
// Green and red are deliberately left out: they mean throttle and brake.
// With five or six laps, slots 5-6 can look like an earlier one to a
// colour-blind reader, so laps are always named in legends and tooltips too.
export const LAP_SLOTS = 6;

// ---- Colours the user picked, remembered in this browser ----
//   laps:     { slot: "#rrggbb" } - a compared lap's colour. Overriding the
//             --lap-N variable recolours that lap EVERYWHERE: charts, maps, the
//             compared-laps table. The pick is kept in both themes.
//   channels: { "Throttle Pos": "#rrggbb" } - a channel's colour in overlay rows
//             on the reference lap; "Throttle Pos@1" is its colour on the 2nd
//             compared lap, "@2" the 3rd, and so on.
//   order:    [slot, ...] - which lap colours are handed out first
//   byCar:    true = colour by car: each car takes the next colour in `order`
//             and its laps get lighter and darker shades of it

const KEY = "traceColours";
export const DEFAULT_ORDER = [0, 1, 2, 3, 4, 5];
const EMPTY = { laps: {}, channels: {}, order: DEFAULT_ORDER, byCar: false };

// A saved order is only used if it still holds every slot exactly once
const validOrder = (order) =>
  Array.isArray(order) && order.length === LAP_SLOTS && DEFAULT_ORDER.every((s) => order.includes(s)) ? order : DEFAULT_ORDER;

export function loadColours() {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? "{}");
    return { laps: { ...saved.laps }, channels: { ...saved.channels }, order: validOrder(saved.order), byCar: !!saved.byCar };
  } catch {
    return { ...EMPTY }; // corrupt entry or storage off: start fresh
  }
}

export function saveColours(colours) {
  try { localStorage.setItem(KEY, JSON.stringify(colours)); } catch { /* storage off: just not remembered */ }
}

// ---- Mixing colours ----
const rgb = (color) => {
  const hex = color.match(/^#([0-9a-f]{6})$/i);
  if (hex) return [0, 2, 4].map((i) => parseInt(hex[1].slice(i, i + 2), 16));
  const m = color.match(/\d+(\.\d+)?/g);
  return m ? m.slice(0, 3).map(Number) : [136, 136, 136];
};
const hexOf = (values) => "#" + values.map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");
// "#rrggbb" for the colour pickers, whatever form the colour came in
export const toHex = (color) => hexOf(rgb(color));
// t of the way from colour a to colour b (0 = a, 1 = b)
export const mixColours = (a, b, t) => {
  const x = rgb(a), y = rgb(b);
  return hexOf(x.map((v, i) => v + (y[i] - v) * t));
};

// ---- Colour by car ----
// Cars in the order they first appear in the compared laps
export const carsOf = (laps) => [...new Set(laps.map((l) => l.car))];
// How a car's 1st, 2nd, 3rd... lap is shaded: + = towards white, - = towards black
const SHADES = [0, 0.45, -0.35, 0.7, -0.55, 0.85];
const shade = (base, n) => {
  const t = SHADES[n % SHADES.length];
  return t === 0 ? toHex(base) : mixColours(base, t > 0 ? "#ffffff" : "#000000", Math.abs(t));
};
// The palette slot whose colour a car takes
export const carSlot = (colours, laps, car) => colours.order[Math.max(0, carsOf(laps).indexOf(car)) % LAP_SLOTS];
// { slot: colour } for every compared lap: its car's colour, shaded by how many
// of that car's laps came before it. A colour picked for a palette slot changes that car's colour.
function carColours(colours, laps, themeColour) {
  const seen = {};
  return Object.fromEntries(laps.map((lap) => {
    const n = seen[lap.car] = (seen[lap.car] ?? -1) + 1;
    const slot = carSlot(colours, laps, lap.car);
    return [lap.slot, shade(colours.laps[slot] ?? themeColour(slot), n)];
  }));
}

// The first free colour slot for a new compared lap, following the chosen order
export const freeSlot = (colours, laps) => colours.order.find((s) => !laps.some((l) => l.slot === s));

// Put the lap colours into the page's CSS variables: the picked ones, or the
// car colours. Overrides from last time are taken away first, so the theme's
// default comes back (and car colours are worked out from the theme's palette).
const applied = new Set();
export function applyLapColours(colours, laps) {
  const root = document.documentElement.style;
  for (const slot of applied) root.removeProperty(`--lap-${Number(slot) + 1}`);
  applied.clear();
  const themeColour = (slot) => getComputedStyle(document.documentElement).getPropertyValue(`--lap-${slot + 1}`).trim();
  // --palette-N: the colours as they stand before car shading, for the colour-order picker
  for (let slot = 0; slot < LAP_SLOTS; slot++) root.setProperty(`--palette-${slot + 1}`, colours.laps[slot] ?? themeColour(slot));
  const set = colours.byCar ? carColours(colours, laps, themeColour) : colours.laps;
  for (const [slot, hex] of Object.entries(set)) {
    root.setProperty(`--lap-${Number(slot) + 1}`, hex);
    applied.add(slot);
  }
}

export const NO_COLOURS = EMPTY;
