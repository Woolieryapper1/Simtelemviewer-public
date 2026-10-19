// Number and date formatting used across the app.
const LOCALE = "en-GB";

export const fmtLap = (s) => (s == null ? "–" : `${Math.floor(s / 60)}:${(s % 60).toFixed(3).padStart(6, "0")}`);
export const fmtSplit = (s) => (s == null ? "–" : s.toFixed(3));
export const fmtDuration = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
export const fmtDay = (iso) =>
  iso ? new Date(iso).toLocaleDateString(LOCALE, { weekday: "short", day: "numeric", month: "short" }) : "Unknown date";
export const fmtDate = (iso) =>
  iso ? new Date(iso).toLocaleString(LOCALE, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
export const signed = (v, d = 3) => (v == null ? "–" : `${v >= 0 ? "+" : ""}${v.toFixed(d)}`);
export const num = (v, d = 1) => (v == null ? "–" : v.toFixed(d));
// "02BSUFSTPAPA L2" - short enough for chart legends
export const shortLabel = (lap) => `${lap.driver.slice(0, 12)} L${lap.lap}`;
export const fmtTime =(iso) =>
  iso ? new Date(iso).toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit" }) : "";
