"""The web API, which also serves the dashboard's web pages. Start it with the
fs-telemetry command (see cli.py), or while developing with:

    .venv/Scripts/python -m uvicorn fs_telemetry.main:app --reload

then open http://127.0.0.1:8000 for the dashboard, or
http://127.0.0.1:8000/docs to try every API endpoint in the browser.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, FastAPI, HTTPException, Path as PathParam, Query
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .aero import AeroMap, aero_map
from .catalog import Catalog, SessionSummary, session_key
from .compare import CompareError, Comparison, LapRef, compare_laps
from .dampers import Dampers, DamperSettings, compute_dampers
from .ingest.motec_ld import LdSession
from .laps import best_lap, lap_infos, resample_lap
from .layouts import Layout, LayoutIn, LayoutStore
from .metrics import MetricSettings, Metrics, compute_metrics
from .notes import ImportResult, LapNoteIn, NotesStore, SessionNote, SessionNoteIn
from .setups import AvailableSetup, Setup, SetupComparison, SetupError, SetupIn, SetupLibrary, SetupSummary
from .sectors import sector_boundaries
from .setup_notes import SettingChange, SetupNote, SetupNoteIn, SetupNotes, SetupNotesOverview
from .stint import LapFilter, Stint, stint

# The built React app (made from web/ by `npm --prefix web run build`). It's committed
# and shipped inside the package, so people installing the app don't need Node.js.
WEB_DIR = Path(__file__).resolve().parent / "web"

app = FastAPI(title="FS Sim Telemetry API")
api = APIRouter(prefix="/api")  # all data endpoints live under /api/...
catalog = Catalog()
layouts = LayoutStore()
setup_library = SetupLibrary()
session_notes = NotesStore()


def setup_notes() -> SetupNotes:
    """The setup log, next to whichever library is in use (tests swap the library for a temporary one)."""
    return SetupNotes(setup_library)
LayoutName = PathParam(min_length=1, max_length=60)


# --- Response shapes (these also generate the /docs page) -----------------


class ChannelInfo(BaseModel):
    name: str
    unit: str
    freq_hz: int
    maths: bool  # calculated by us (app/maths.py) rather than logged
    approximate: bool  # an estimate (e.g. aero loads) - show it as such
    description: str
    basis: str = ""  # calculated channels: what they rest on (known car facts, or learned from the run)


class LapOut(BaseModel):
    number: int
    lap_time_s: float
    kind: str  # "out", "timed" or "incomplete"
    invalid: bool
    is_best: bool


class LapData(BaseModel):
    session_id: str
    lap: int
    step_m: float
    units: dict[str, str]
    data: dict[str, list[float]]  # "Distance", "Time", then each channel


# --- Helpers --------------------------------------------------------------


def _session(session_id: str) -> LdSession:
    s = catalog.get(session_id)
    if s is None:
        raise HTTPException(404, f"No session '{session_id}'")
    return s


def _lap_data(session_id: str, lap: int, channels: list[str], step_m: float) -> tuple[LdSession, dict]:
    s = _session(session_id)
    if not 1 <= lap <= len(s.laps):
        raise HTTPException(404, f"Lap {lap} not found (session has {len(s.laps)} laps)")
    unknown = [c for c in channels if c not in s.channels]
    if unknown:
        raise HTTPException(404, f"Unknown channel(s): {', '.join(unknown)}")
    return s, resample_lap(s, lap, channels, step_m)


def _units(s: LdSession, names) -> dict[str, str]:
    return {"Distance": "m", "Time": "s", **{n: s.channels[n].unit for n in names}}


# --- Endpoints ------------------------------------------------------------


@api.get("/sessions")
def list_sessions(
    driver: str | None = None, car: str | None = None, track: str | None = None
) -> list[SessionSummary]:
    """All sessions, newest first. Optional filters match exactly."""
    out = catalog.summaries()
    if driver:
        out = [s for s in out if s.driver == driver]
    if car:
        out = [s for s in out if s.car == car]
    if track:
        out = [s for s in out if s.track == track]
    return out


# --- Team library: runs shared in a folder the whole team syncs (see shared.py) ---


class TeamLibrary(BaseModel):
    enabled: bool  # the folder is set and found
    folder: str | None
    me: str  # my folder's name in it
    people: list[str]  # everyone with a folder in it


@api.get("/team")
def team_library() -> TeamLibrary:
    lib = catalog.shared
    return TeamLibrary(enabled=lib.enabled, folder=str(lib.root) if lib.root else None, me=lib.me, people=lib.people())


def _team_call(fn, session_id: str) -> SessionSummary:
    try:
        s = fn(session_id)
    except (ValueError, OSError) as e:
        raise HTTPException(422, str(e))
    if s is None:
        raise HTTPException(404, f"No session '{session_id}'")
    return s


@api.post("/sessions/{session_id}/share")
def share_session(session_id: str) -> SessionSummary:
    """Copy one of my runs into my folder of the team library, so teammates see it."""
    return _team_call(catalog.share, session_id)


@api.delete("/sessions/{session_id}/share")
def unshare_session(session_id: str) -> SessionSummary:
    """Take one of my runs out of the team library (the copy on this PC stays)."""
    return _team_call(catalog.unshare, session_id)


@api.get("/sessions/{session_id}/channels")
def list_channels(session_id: str) -> list[ChannelInfo]:
    s = _session(session_id)
    return [ChannelInfo(name=c.name, unit=c.unit, freq_hz=c.freq, maths=c.maths, approximate=c.approximate,
                        description=c.description, basis=c.basis)
            for c in s.channels.values()]


@api.get("/sessions/{session_id}/laps")
def list_laps(session_id: str) -> list[LapOut]:
    infos = lap_infos(_session(session_id))
    best = best_lap(infos)
    return [
        LapOut(number=l.number, lap_time_s=round(l.lap_time, 3), kind=l.kind,
               invalid=l.invalid, is_best=l is best)
        for l in infos
    ]


@api.get("/sessions/{session_id}/laps/{lap}/data")
def lap_data(
    session_id: str,
    lap: int,
    channels: list[str] = Query(default=["Ground Speed", "Throttle Pos", "Brake Pos"]),
    step_m: float = Query(default=1.0, gt=0.05, le=50),
) -> LapData:
    """Chosen channels for one lap, sampled every `step_m` metres of lap distance."""
    s, data = _lap_data(session_id, lap, channels, step_m)
    return LapData(
        session_id=session_id, lap=lap, step_m=step_m, units=_units(s, channels),
        data={k: [round(float(x), 4) for x in v] for k, v in data.items()},
    )


@api.get("/sessions/{session_id}/laps/{lap}/data.csv")
def lap_data_csv(
    session_id: str,
    lap: int,
    channels: list[str] = Query(default=["Ground Speed", "Throttle Pos", "Brake Pos"]),
    step_m: float = Query(default=1.0, gt=0.05, le=50),
) -> Response:
    """Same as /data but as a CSV download, for Excel / MATLAB."""
    s, data = _lap_data(session_id, lap, channels, step_m)
    units = _units(s, channels)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([f"{k} [{units[k]}]" if units[k] else k for k in data])
    w.writerows(zip(*(v.round(4) for v in data.values())))
    filename = f"{session_id}_lap{lap}.csv"
    return Response(
        buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class StintQuery(MetricSettings, LapFilter):
    # FastAPI only spreads a model into separate query parameters when it's the
    # only query input, so the lap filter is folded into the settings model.
    pass


@api.get("/sessions/{session_id}/stint")
def session_stint(session_id: str, settings: Annotated[StintQuery, Query()]) -> Stint:
    """Per-lap fuel, tyre wear, temps and driver inputs for a whole run, plus averages.
    Driver-input thresholds come from the same settings as /metrics; the lap filter
    (include_partial, slow_filter, slow_pct) says which laps the averages are taken over."""
    s = _session(session_id)
    flags = session_notes.flags(session_key(s))  # laps the team marked "use" / "ignore"
    return stint(s, session_id, settings, sector_boundaries(catalog, s.track)[0], settings, flags)


class MetricsQuery(MetricSettings):
    # FastAPI only spreads a model into separate query parameters when it's the
    # only query input, so the laps live inside the model too.
    lap: list[str] = Field(description="Laps as '<session id>:<lap number>'. The first is the reference.")


@api.get("/sessions/{session_id}/aero")
def session_aero(session_id: str) -> AeroMap:
    """APPROXIMATE downforce, downforce coefficient (ClA), aero balance and ride heights by speed
    band, from steady running in this run. AC doesn't log aero loads - these are estimated from the
    tyre loads (see app/aero.py), and the response carries approximate=true and a note saying so."""
    result = aero_map(_session(session_id))
    if result is None:
        raise HTTPException(422, "This run doesn't have what aero needs (tyre loads, slow running to weigh the car)")
    return result


@api.get("/metrics")
def metrics(q: Annotated[MetricsQuery, Query()]) -> Metrics:
    """Corner gating, balance, G-G points and driver inputs for the given laps.
    Every definition (speed bands, thresholds) is a query parameter - see MetricSettings."""
    settings = MetricSettings(**q.model_dump(exclude={"lap"}))
    try:
        return compute_metrics(catalog, [LapRef.parse(x) for x in q.lap], settings)
    except CompareError as e:
        raise HTTPException(422, str(e))


class DampersQuery(DamperSettings, MetricSettings):
    # Gate definitions come from the same settings as /metrics; laps inside the model, as for MetricsQuery
    lap: list[str] = Field(description="Laps as '<session id>:<lap number>'. The first is the reference (and sets the gates).")


@api.get("/dampers")
def dampers(q: Annotated[DampersQuery, Query()]) -> Dampers:
    """Damper velocity histograms per corner (+ bump, - rebound), % of time in each speed band, mean and
    peak bump / rebound speed and the bump:rebound time ratio, for each lap or each lap's whole run,
    optionally only in one Metrics gate. AC doesn't log damper velocity: it's estimated from suspension
    travel and the response says so."""
    data = q.model_dump(exclude={"lap"})
    try:
        settings = DamperSettings(**{k: data[k] for k in DamperSettings.model_fields})
        gating = MetricSettings(**{k: data[k] for k in MetricSettings.model_fields})
        return compute_dampers(catalog, [LapRef.parse(x) for x in q.lap], settings, gating)
    except CompareError as e:
        raise HTTPException(422, str(e))


# --- Lap comparison: track dominance ---------------------------------------


def _compare(lap: list[str], sectors: int) -> Comparison:
    try:
        return compare_laps(catalog, [LapRef.parse(x) for x in lap], sectors)
    except CompareError as e:
        raise HTTPException(422, str(e))


LapsQuery = Query(description="Laps to compare as '<session id>:<lap number>'. The first is the reference.")
SectorsQuery = Query(default=25, ge=3, le=100, description="Number of equal-length mini-sectors")


@api.get("/compare")
def compare(lap: list[str] = LapsQuery, sectors: int = SectorsQuery) -> Comparison:
    """Mini-sector times, who won each one, and the racing line for the map."""
    return _compare(lap, sectors)


@api.get("/compare.csv")
def compare_csv(lap: list[str] = LapsQuery, sectors: int = SectorsQuery) -> Response:
    """The mini-sector table as a CSV, e.g. for post-race reports."""
    c = _compare(lap, sectors)
    names = [f"{l.driver} L{l.lap}" for l in c.laps]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["Mini-sector", "Start [m]", "End [m]", *[f"{n} [s]" for n in names], "Fastest", "Margin [s]"])
    for k, sec in enumerate(c.sectors):
        w.writerow([sec.number, sec.start_m, sec.end_m, *[l.sector_times_s[k] for l in c.laps],
                    names[sec.winner], sec.margin_s])
    w.writerow(["Lap", 0, c.track_length_m, *[l.lap_time_s for l in c.laps], "", ""])
    w.writerow(["Theoretical best", "", "", c.theoretical_best_s])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="dominance_{c.track}.csv"'})


# --- Session notes, tags and lap flags ---------------------------------------
# Filed under each run's stable key (SessionSummary.key), so they survive the run
# being exported again or renamed. The dashboard joins them to /sessions by key.


def _note_label(session_id: str) -> tuple[str, dict]:
    """(stable key, what to store about the run) for a run, or 404."""
    s = catalog.summary(session_id)
    if s is None:
        raise HTTPException(404, f"No session '{session_id}'")
    started = s.started.isoformat() if s.started else None
    return s.key, dict(session_id=s.id, source=s.source, driver=s.driver, car=s.car, track=s.track, started=started)


@api.get("/notes")
def list_notes() -> dict[str, SessionNote]:
    """Every run's notes, by the run's stable key (the `key` in /sessions)."""
    return session_notes.all()


@api.get("/notes/tags")
def note_tags() -> list[str]:
    """Tags to suggest: the ones in use (most used first), then a few common ones."""
    return session_notes.tags()


@api.patch("/sessions/{session_id}/notes")
def edit_session_notes(session_id: str, body: SessionNoteIn) -> SessionNote:
    """Change a run's notes, tags or star. Only the fields sent change."""
    key, label = _note_label(session_id)
    return session_notes.update(key, label, body)


@api.patch("/sessions/{session_id}/laps/{lap}/notes")
def edit_lap_notes(session_id: str, lap: int, body: LapNoteIn) -> SessionNote:
    """Change one lap's note or flag ("use", "ignore", or null for automatic).
    The Stint averages follow the flags."""
    key, label = _note_label(session_id)
    count = catalog.summary(session_id).lap_count
    if not 1 <= lap <= count:
        raise HTTPException(404, f"Lap {lap} not found (session has {count} laps)")
    return session_notes.update_lap(key, label, lap, body)


@api.get("/notes/export")
def export_notes() -> Response:
    """All the notes as one file, to import on another PC."""
    body = json.dumps(session_notes.export(), indent=1, ensure_ascii=False)
    filename = f"session-notes-{date.today().isoformat()}.json"
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@api.post("/notes/import")
def import_notes(body: dict) -> ImportResult:
    """Merge an exported notes file into these notes: for a run in both, the copy edited last wins."""
    try:
        return session_notes.import_file(body)
    except ValueError as e:
        raise HTTPException(422, str(e))


# --- Setup library -----------------------------------------------------------


class ImportIn(BaseModel):
    path: str = Field(description="Path inside the AC setups folder, as listed by /setups/available")


class NotesIn(BaseModel):
    notes: str = Field(max_length=4000)


def _setup_call(fn, *args):
    try:
        return fn(*args)
    except SetupError as e:
        raise HTTPException(422, str(e))


@api.get("/setups")
def list_setups() -> list[SetupSummary]:
    """Every setup in the library (all versions)."""
    return setup_library.all()


@api.get("/setups/available")
def available_setups() -> list[AvailableSetup]:
    """Setup files in the AC setups folder on this server, and whether they're already stored."""
    return setup_library.available()


@api.post("/setups/import")
def import_setup(body: ImportIn) -> Setup:
    s = _setup_call(setup_library.import_file, body.path)
    setup_notes().write_markdown()  # a new version of a noted setup: its file lists the changes
    return s


@api.post("/setups")
def upload_setup(body: SetupIn) -> Setup:
    """Add a setup from the text of an .ini file (the dashboard reads the file for you)."""
    s = _setup_call(setup_library.upload, body)
    setup_notes().write_markdown()
    return s


# --- Setup notes: a dated log per setup (what changed, why, driver feedback) -----
# Kept as JSON by the app and rewritten as Markdown files after each change (see
# setup_notes().py), so teammates can read them without the app; also as CSV for Excel.


def _setup_or_404(setup_id: str) -> None:
    if setup_library.get(setup_id) is None:
        raise HTTPException(404, f"No setup '{setup_id}'")


@api.get("/setups/notes")
def list_setup_notes() -> SetupNotesOverview:
    """Every setup's log entries (newest first), and the folder the Markdown copies are written to."""
    return setup_notes().overview()


@api.get("/setups/notes.csv")
def setup_notes_csv(id: list[str] = Query(default=[], description="Only these setups (all if none)")) -> Response:
    """The setup log as a CSV that opens in Excel: one row per entry."""
    return Response(setup_notes().csv_text(id), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="setup-notes-{date.today().isoformat()}.csv"'})


@api.get("/setups/{setup_id}/changes")
def setup_changes(setup_id: str) -> list[SettingChange]:
    """Settings that differ from the previous version of the same setup (empty for a first version)."""
    _setup_or_404(setup_id)
    return setup_notes().changes(setup_id)


@api.post("/setups/{setup_id}/notes")
def add_setup_note(setup_id: str, body: SetupNoteIn) -> SetupNote:
    _setup_or_404(setup_id)
    return setup_notes().add(setup_id, body)


@api.put("/setups/{setup_id}/notes/{entry_id}")
def edit_setup_note(setup_id: str, entry_id: str, body: SetupNoteIn) -> SetupNote:
    entry = setup_notes().edit(setup_id, entry_id, body)
    if entry is None:
        raise HTTPException(404, f"No note '{entry_id}' on setup '{setup_id}'")
    return entry


@api.delete("/setups/{setup_id}/notes/{entry_id}", status_code=204)
def delete_setup_note(setup_id: str, entry_id: str) -> None:
    if not setup_notes().delete(setup_id, entry_id):
        raise HTTPException(404, f"No note '{entry_id}' on setup '{setup_id}'")


@api.get("/setups/compare")
def compare_setups(id: list[str] = Query(description="Setup ids; the first is the reference")) -> SetupComparison:
    """Every field side by side, with the ones that differ marked."""
    return _setup_call(setup_library.compare, id)


@api.get("/setups/{setup_id}")
def get_setup(setup_id: str) -> Setup:
    s = setup_library.get(setup_id)
    if s is None:
        raise HTTPException(404, f"No setup '{setup_id}'")
    return s


@api.patch("/setups/{setup_id}")
def edit_setup_notes(setup_id: str, body: NotesIn) -> Setup:
    s = setup_library.set_notes(setup_id, body.notes)
    if s is None:
        raise HTTPException(404, f"No setup '{setup_id}'")
    setup_notes().write_markdown()  # the description is in the Markdown too
    return s


@api.delete("/setups/{setup_id}", status_code=204)
def delete_setup(setup_id: str) -> None:
    if not setup_library.delete(setup_id):
        raise HTTPException(404, f"No setup '{setup_id}'")
    setup_notes().forget(setup_id)


# --- Saved chart layouts --------------------------------------------------
# GET reads, PUT creates-or-replaces, DELETE removes: the standard pattern
# for "things you can save" in a web API.


@api.get("/layouts")
def list_layouts() -> list[Layout]:
    """Built-in layouts first, then ones saved by the team."""
    return layouts.all()


@api.put("/layouts/{name}")
def save_layout(body: LayoutIn, name: str = LayoutName) -> Layout:
    try:
        return layouts.save(name, body.panels)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@api.delete("/layouts/{name}", status_code=204)
def delete_layout(name: str = LayoutName) -> None:
    try:
        found = layouts.delete(name)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    if not found:
        raise HTTPException(404, f"No saved layout '{name}'")


class WebPages(StaticFiles):
    """The dashboard's files, sent with "Cache-Control: no-cache" so the browser checks
    for a newer copy every time. Without it, browsers may keep showing an old saved copy
    of the page after the app is updated. Checking is cheap: an unchanged file comes back
    as a tiny "not modified" reply."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


# Register the API first, then serve the dashboard's files (index.html etc.)
# for every other URL. Order matters: the first match wins.
app.include_router(api)
if (WEB_DIR / "index.html").exists():
    app.mount("/", WebPages(directory=WEB_DIR, html=True), name="web")
else:

    @app.get("/", include_in_schema=False)
    def not_built() -> HTMLResponse:
        return HTMLResponse("<p>The web pages haven't been built. Run <code>npm --prefix web run build</code>, "
                            "or use the dev server (<code>npm --prefix web run dev</code>).</p><p>The API is at /docs.</p>")
