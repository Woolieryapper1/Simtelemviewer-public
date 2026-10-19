"""Print a summary of a MoTeC .ld file: session info, laps, and all channels.

Usage (from the FS2627 folder):
    .venv/Scripts/python backend/scripts/inspect_ld.py "path/to/file.ld"
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fs_telemetry.ingest.motec_ld import read_ld  # noqa: E402

s = read_ld(sys.argv[1])
print(f"{s.driver} | {s.car} | {s.track} | {s.date} {s.time} | {s.duration:.1f}s")
print("\nLaps:")
for lap in s.laps:
    m, sec = divmod(lap.lap_time, 60)
    print(f"  {lap.number:>2}  {int(m)}:{sec:06.3f}")

print(f"\n{len(s.channels)} channels (name [unit] @Hz  min..max):")
for c in s.channels.values():
    print(f"  {c.name:32} [{c.unit:6}] @{c.freq:>2}Hz  {c.data.min():10.3f} .. {c.data.max():10.3f}")
