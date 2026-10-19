import { useEffect, useState } from "react";
import { api } from "./api.js";

const STEP_M = 2; // metres between samples on the distance axis

// Keeps the channel data of every compared lap, fetching only what's missing:
// adding a channel downloads just that channel, not everything again.
// Returns { [lapKey]: { data: {Distance, Time, <channel>: [...]}, units } }.
export default function useLapData(laps, channels) {
  const [cache, setCache] = useState({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const want = [...new Set(["Ground Speed", ...channels])];
  const lapsKey = laps.map((l) => l.key).join(",");
  const wantKey = want.join(",");

  useEffect(() => {
    const jobs = laps
      .map((lap) => ({ lap, missing: want.filter((c) => !cache[lap.key]?.data[c]) }))
      .filter((j) => j.missing.length);
    if (!jobs.length) return;
    setLoading(true);
    setError(null);
    Promise.all(jobs.map(async ({ lap, missing }) => {
      const r = await api.lapData(lap.sessionId, lap.lap, missing, STEP_M);
      // Merge into whatever's already there for this lap
      setCache((prev) => ({
        ...prev,
        [lap.key]: { data: { ...prev[lap.key]?.data, ...r.data }, units: { ...prev[lap.key]?.units, ...r.units } },
      }));
    }))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [lapsKey, wantKey]); // re-run when the laps or wanted channels change

  return { cache, loading, error };
}
