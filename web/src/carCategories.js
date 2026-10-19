// Browsing labels only: never used for maths or car setup assumptions.
// Prefer specific classes before broader name matches (GT3 Cup is not GT3).
export function carCategory(car) {
  const name = car.toLowerCase();
  if (/mft02/.test(name)) return "Formula Student";
  if (/gt3 cup|911 cup|mx-5 cup/.test(name)) return "Cup cars";
  if (/gt3|911 gt3 r/.test(name)) return "GT3";
  if (/gt4/.test(name)) return "GT4";
  if (/gt1/.test(name)) return "GT1";
  if (/v-series\.r|963|499p|gr010|lmdh|lmh|hypercar|gtp/.test(name)) return "GTP / Hypercar";
  if (/lmp2|p217/.test(name)) return "LMP2";
  if (/lmp3|js p320/.test(name)) return "LMP3";
  if (/formula|dallara ir|f1600/.test(name)) return "Formula";
  if (/grc|rallycross/.test(name)) return "Rallycross";
  if (/stock|nascar/.test(name)) return "Stock cars";
  if (/supercars/.test(name)) return "Supercars";
  if (/m2.*racing|tcr|touring/.test(name)) return "Touring cars";
  return "Other";
}
