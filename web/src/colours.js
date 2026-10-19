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
//   channels: { "Throttle Pos": "#rrggbb" } - a channel's colour in overlay rows.

const KEY = "traceColours";
const EMPTY = { laps: {}, channels: {} };

export function loadColours() {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? "{}");
    return { laps: { ...saved.laps }, channels: { ...saved.channels } };
  } catch {
    return { ...EMPTY }; // corrupt entry or storage off: start fresh
  }
}

export function saveColours(colours) {
  try { localStorage.setItem(KEY, JSON.stringify(colours)); } catch { /* storage off: just not remembered */ }
}

// Put the lap colour overrides into the page's CSS variables (and take away any
// that were reset, so the theme's default comes back)
const applied = new Set();
export function applyLapColours(laps) {
  const root = document.documentElement.style;
  for (const slot of applied) if (!(slot in laps)) root.removeProperty(`--lap-${Number(slot) + 1}`);
  applied.clear();
  for (const [slot, hex] of Object.entries(laps)) {
    root.setProperty(`--lap-${Number(slot) + 1}`, hex);
    applied.add(slot);
  }
}

export const NO_COLOURS = EMPTY;
