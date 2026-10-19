"""Setup library: store AC and iRacing setups and compare them.

An AC setup file (.ini) is a list of sections like

    [CAMBER_LF]
    VALUE=-16

plus [CAR] MODEL=<car>. Values are the setup screen's raw numbers ("clicks" for
most cars) - turning them into real units (degrees, N/mm) needs the car's own
data/setup.ini, which we don't have yet.

iRacing has no setup files we can read (.sto is its own binary format), but every
.ibt telemetry file holds the setup the car ran with, in real units as shown in
the garage ("-4.4 deg", "180 N/mm"). So iRacing setups are imported from runs.

Every import or upload becomes a new, unchanging library entry. Saving a setup
with the same car + track + name again creates the next *version*, so old
versions are kept and can be compared against the new one. Only the notes can
be edited afterwards.
"""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from .config import AC_SETUPS_DIR, DATA_DIR, IRACING_DIR
from .ingest.ibt import read_session_info, setup_values, who_where


class SetupError(ValueError):
    pass


# --- Parsing ---------------------------------------------------------------


def parse_setup_ini(text: str) -> tuple[str, dict[str, float | str]]:
    """INI text -> (car model, {field: value}). Numbers come back as numbers."""
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str  # keep key case
    try:
        cp.read_string(text)
    except configparser.Error as e:
        raise SetupError(f"Not a valid setup file: {e}") from e
    values: dict[str, float | str] = {}
    for section in cp.sections():
        raw = cp[section].get("VALUE")
        if raw is None:
            continue
        try:
            values[section] = float(raw) if "." in raw else int(raw)
        except ValueError:
            values[section] = raw.strip()
    if not values:
        raise SetupError("No setup values found - is this an AC setup .ini?")
    car = cp["CAR"].get("MODEL", "").strip() if cp.has_section("CAR") else ""
    return car, values


def _mtime(f: Path) -> str:
    return datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")


def _fingerprint(values: dict) -> str:
    return hashlib.sha1(json.dumps(values, sort_keys=True).encode()).hexdigest()[:12]


# --- Grouping fields for display ---------------------------------------------
# (group, regex) - first match wins. Corners: LF/RF/LR/RR in AC = FL/FR/RL/RR.

GROUPS = [
    ("Tyres", r"^(TYRES|PRESSURE_)"),
    ("Alignment", r"^(CAMBER_|TOE_OUT_|CASTER)"),
    ("Springs & ride height", r"^(SPRING_RATE_|ROD_LENGTH_|PACKER|BUMP_STOP)"),
    ("Dampers", r"^DAMP_"),
    ("Anti-roll bars", r"^ARB_"),
    ("Brakes", r"^(FRONT_BIAS|BRAKE_)"),
    ("Aero", r"^WING_"),
    ("Drivetrain", r"^(FINAL_RATIO|INTERNAL_GEAR|DIFF_|GEAR_)"),
    ("Fuel & electronics", r"^(FUEL|ABS|TRACTION|ENGINE|ERS|KERS|LIMITER)"),
]


# iRacing fields are paths through its garage screens, e.g. "Chassis_LeftFront_Camber":
# the first part is the screen (used as the group), corners/axles become FL.../(front).
IRACING_CORNERS = {"LeftFront": "FL", "RightFront": "FR", "LeftRear": "RL", "RightRear": "RR"}


def is_iracing(field: str) -> bool:
    return bool(re.match(r"^[A-Z][a-z]", field))  # AC's fields are ALL CAPS


def _words(name: str) -> str:
    """'SpringPerchOffset' -> 'spring perch offset'"""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).lower()


def group_of(field: str) -> str:
    if is_iracing(field):
        return _words(field.split("_")[0]).capitalize()
    return next((g for g, rx in GROUPS if re.match(rx, field)), "Other")


def pretty(field: str) -> str:
    """'DAMP_FAST_BUMP_LF' -> 'Damp fast bump FL';
    'Chassis_LeftFront_Camber' -> 'Camber FL'; 'Chassis_Front_ArbSetting' -> 'Front · Arb setting'"""
    if is_iracing(field):
        parts, corner = [], ""
        for part in field.split("_")[1:]:  # the first part is the group
            if c := next((v for k, v in IRACING_CORNERS.items() if part.startswith(k)), ""):
                corner = f" {c}"  # e.g. "LeftFront" or "LeftFrontDamper"
            elif not parts or _words(part) != parts[-1].lower():  # "TireType_TireType" -> once
                parts.append(_words(part).capitalize())
        return " · ".join(parts) + corner
    corner = {"_LF": " FL", "_RF": " FR", "_LR": " RL", "_RR": " RR"}
    for suffix, name in corner.items():
        if field.endswith(suffix):
            return field[: -len(suffix)].replace("_", " ").capitalize() + name
    return field.replace("_", " ").capitalize()


# --- Library -------------------------------------------------------------------


class SetupSummary(BaseModel):
    id: str
    name: str
    car: str
    track: str
    version: int
    saved_at: str  # ISO time it entered the library
    file_modified: str | None = None  # when the setup file itself was last saved (in AC)
    source: str  # where it came from
    notes: str
    fingerprint: str  # same values -> same fingerprint


class Setup(SetupSummary):
    values: dict[str, float | int | str]


class SetupIn(BaseModel):
    """An upload from the dashboard: the .ini file's text plus labels."""

    name: str = Field(min_length=1, max_length=80)
    track: str = Field(default="generic", max_length=80)
    car: str = Field(default="", max_length=80, description="Taken from the file's [CAR] section if left empty")
    notes: str = Field(default="", max_length=4000)
    ini_text: str = Field(min_length=1, max_length=200_000)
    file_modified: str | None = Field(default=None, description="ISO time the file was last saved, if known")


class AvailableSetup(BaseModel):
    """A setup that can be imported: an .ini in the AC setups folder, or the setup an iRacing run used."""

    path: str  # relative to the AC setups folder, or "iracing:<run id>"
    source: str = "ac"  # "ac" or "iracing"
    car: str
    track: str
    name: str
    modified: str
    in_library: bool  # an identical copy (same car, name and values) is already stored


class CompareRow(BaseModel):
    field: str
    label: str
    group: str
    values: list[float | int | str | None]  # one per setup, None = setup doesn't have this field
    changed: bool  # not all setups agree


class SetupComparison(BaseModel):
    setups: list[SetupSummary]
    rows: list[CompareRow]
    changed_count: int


class SetupLibrary:
    def __init__(self, path: Path | None = None, ac_dir: Path | None = None, iracing_dir: Path | None = None):
        self.path = path or DATA_DIR / "setups" / "library.json"
        self.ac_dir = ac_dir or AC_SETUPS_DIR
        self.iracing_dir = iracing_dir or IRACING_DIR

    # storage: one JSON file, written atomically (temp file + swap) like layouts
    def _read(self) -> list[dict]:
        items = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []
        for s in items:  # entries stored before file dates were recorded: look them up if we can
            if "file_modified" not in s:
                f = self.ac_dir / s["source"].removeprefix("AC folder: ")
                s["file_modified"] = _mtime(f) if s["source"].startswith("AC folder: ") and f.is_file() else None
        return items

    def _write(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=1)
        os.replace(tmp, self.path)

    def all(self) -> list[SetupSummary]:
        return [SetupSummary(**{k: v for k, v in s.items() if k != "values"}) for s in self._read()]

    def get(self, setup_id: str) -> Setup | None:
        return next((Setup(**s) for s in self._read() if s["id"] == setup_id), None)

    def add(self, name: str, car: str, track: str, values: dict, source: str, notes: str = "",
            file_modified: str | None = None) -> Setup:
        items = self._read()
        same_name = [s for s in items if (s["car"], s["track"], s["name"]) == (car, track, name)]
        fp = _fingerprint(values)
        if same_name and same_name[-1]["fingerprint"] == fp:
            return Setup(**same_name[-1])  # unchanged since the last version: nothing new to store
        setup = Setup(
            id=uuid.uuid4().hex[:10], name=name, car=car, track=track, version=len(same_name) + 1,
            saved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), file_modified=file_modified, source=source,
            notes=notes, fingerprint=fp, values=values,
        )
        items.append(setup.model_dump())
        self._write(items)
        return setup

    def upload(self, body: SetupIn) -> Setup:
        car, values = parse_setup_ini(body.ini_text)
        car = body.car or car
        if not car:
            raise SetupError("Couldn't tell which car this setup is for - fill in the car")
        return self.add(body.name.strip(), car, body.track.strip() or "generic", values, "upload", body.notes,
                        body.file_modified)

    def set_notes(self, setup_id: str, notes: str) -> Setup | None:
        items = self._read()
        for s in items:
            if s["id"] == setup_id:
                s["notes"] = notes
                self._write(items)
                return Setup(**s)
        return None

    def delete(self, setup_id: str) -> bool:
        items = self._read()
        kept = [s for s in items if s["id"] != setup_id]
        if len(kept) == len(items):
            return False
        self._write(kept)
        return True

    # --- importing from the AC setups folder
    def available(self) -> list[AvailableSetup]:
        stored = {(s["car"], s["name"], s["fingerprint"]) for s in self._read()}
        return self._available_ac(stored) + self._available_iracing(stored)

    def _available_ac(self, stored: set) -> list[AvailableSetup]:
        if not self.ac_dir.exists():
            return []
        out = []
        for f in sorted(self.ac_dir.glob("*/*/*.ini")):  # <car>/<track>/<name>.ini
            car, track, name = f.parent.parent.name, f.parent.name, f.stem
            try:
                _, values = parse_setup_ini(f.read_text(encoding="utf-8", errors="replace"))
            except SetupError:
                continue
            out.append(AvailableSetup(
                path=f.relative_to(self.ac_dir).as_posix(), car=car, track=track, name=name,
                modified=_mtime(f),
                in_library=(car, name, _fingerprint(values)) in stored,
            ))
        return out

    # --- importing from iRacing runs
    def _iracing_runs(self) -> dict[str, Path]:
        return {p.stem: p for p in self.iracing_dir.rglob("*.ibt")} if self.iracing_dir.exists() else {}

    @staticmethod
    def _iracing_setup(f: Path) -> tuple[str, str, str, dict, str | None]:
        """(name, car, track, values, run time) of the setup an iRacing run used."""
        info, started = read_session_info(f)
        _, car, track = who_where(info)
        # e.g. "25S3 IMSA Racing Series\Week 2 (Watkins Glen)\CDA S01.sto" -> "CDA S01"
        name = info.get("DriverInfo", {}).get("DriverSetupName", "").split("\\")[-1].removesuffix(".sto") or "iRacing setup"
        when = started.astimezone(timezone.utc).isoformat(timespec="seconds") if started else None
        return name, car, track, setup_values(info), when

    def _available_iracing(self, stored: set) -> list[AvailableSetup]:
        """One entry per different setup, from its newest run (many runs share one setup)."""
        newest: dict[tuple, AvailableSetup] = {}
        for run, f in self._iracing_runs().items():
            try:
                name, car, track, values, started = self._iracing_setup(f)
            except (OSError, ValueError, IndexError):
                continue
            if not values:
                continue
            key = (car, track, name, _fingerprint(values))
            entry = AvailableSetup(path=f"iracing:{run}", source="iracing", car=car, track=track, name=name,
                                   modified=started or _mtime(f), in_library=(car, name, key[3]) in stored)
            if key not in newest or entry.modified > newest[key].modified:
                newest[key] = entry
        return sorted(newest.values(), key=lambda a: (a.car, a.track, a.name, a.modified))

    def import_file(self, rel_path: str) -> Setup:
        if rel_path.startswith("iracing:"):
            run = rel_path.removeprefix("iracing:")
            f = self._iracing_runs().get(run)
            if f is None:
                raise SetupError(f"No iRacing run '{run}' in the iRacing telemetry folder")
            name, car, track, values, started = self._iracing_setup(f)
            if not values:
                raise SetupError(f"iRacing run '{run}' has no setup in it")
            return self.add(name, car, track, values, f"iRacing run: {run}", file_modified=started)
        # Only ever read files inside the AC setups folder (no "../" tricks)
        root = self.ac_dir.resolve()
        f = (root / rel_path).resolve()
        if root not in f.parents or f.suffix.lower() != ".ini" or not f.is_file():
            raise SetupError(f"'{rel_path}' isn't a setup file in the AC setups folder")
        _, values = parse_setup_ini(f.read_text(encoding="utf-8", errors="replace"))
        car, track = f.parent.parent.name, f.parent.name
        return self.add(f.stem, car, track, values, f"AC folder: {rel_path}", file_modified=_mtime(f))

    # --- comparing
    def compare(self, ids: list[str]) -> SetupComparison:
        setups = []
        for i in ids:
            s = self.get(i)
            if s is None:
                raise SetupError(f"No setup '{i}' in the library")
            setups.append(s)
        if not setups:
            raise SetupError("Pick at least one setup")
        # Every field any setup has, in file order, then grouped (sorted() keeps file order within a group)
        seen = list(dict.fromkeys(f for s in setups for f in s.values))
        # AC's groups in their set order, then iRacing's garage screens as they come, then the rest
        group_order = list(dict.fromkeys([g for g, _ in GROUPS] + [group_of(f) for f in seen] + ["Other"]))
        fields = sorted(seen, key=lambda f: group_order.index(group_of(f)))
        rows = []
        for f in fields:
            vals = [s.values.get(f) for s in setups]
            rows.append(CompareRow(field=f, label=pretty(f), group=group_of(f), values=vals,
                                   changed=len({json.dumps(v) for v in vals}) > 1))
        return SetupComparison(
            setups=[SetupSummary(**s.model_dump(exclude={"values"})) for s in setups],
            rows=rows, changed_count=sum(r.changed for r in rows),
        )
