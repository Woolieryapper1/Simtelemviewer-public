# Agent instructions

Instructions for any AI coding agent (Claude Code, Codex, Copilot, Cursor, ...) working in this repo, and a quick
orientation for human contributors. Claude Code reads this through `CLAUDE.md`, which only imports it; other agents
read it directly. `README.md` is the user guide; this file is about working on the code.

**Local notes:** if an `AGENTS.local.md` file exists next to this one, read it too. It holds one person's own
setup (paths, tools, workflow) and is git-ignored. Anything personal goes there, never here.

## What this is
A local web app for analysing sim-racing telemetry: Assetto Corsa (MoTeC `.ld`/`.ldx` from the telemetrick app or
CSP's built-in logger), iRacing (`.ibt`) and CSV. Python/FastAPI backend in `backend/fs_telemetry/`, React + Vite
frontend in `web/`. It runs on the user's PC and uploads nothing.

### Data flow
```
.ld/.ldx (AC)   .ibt (iRacing)   .csv
ingest/motec_ld.py, ingest/csp.py, ingest/ibt.py, ingest/csv_import.py   (+ ingest/ac_track.py: track edges)
        |
    catalog.py      finds and caches sessions in the configured folders (config.py),
                    plus teammates' runs in the team library (shared.py)
        |
    maths.py        derived channels, computed when a session opens (fed by cars.py, aero.py)
        |
    laps.py         splits into laps, resamples by DISTANCE
        |
    compare.py / metrics.py / stint.py / sectors.py / dampers.py / setups.py
        |
    main.py         FastAPI /api/..., also serves the built pages
        |
web/src  -- npm run build -->  backend/fs_telemetry/web  (committed, never edited by hand)
```

| Page | Frontend (`web/src/`) | Backend (`backend/fs_telemetry/`) |
|---|---|---|
| Telemetry | `TelemetryPage.jsx`, `TelemetryChart.jsx` | `compare.py`, `layouts.py` |
| Stint | `StintPage.jsx`, `stintBoxes.jsx` | `stint.py` |
| Metrics | `MetricsPage.jsx`, `DamperBox.jsx` | `metrics.py`, `dampers.py` |
| Track | `TrackPage.jsx` | `compare.py`, `sectors.py`, `ingest/ac_track.py` |
| Setups | `SetupsPage.jsx` | `setups.py`, `setup_notes.py` |

Shared UI pieces and design tokens live in `web/src/ui/` and `theme.css`.

## Commands
- Tests: `.venv\Scripts\python -m pytest` (backtests: `-m backtest`, see README).
- Backend: `.venv\Scripts\python -m uvicorn fs_telemetry.main:app --reload --port 8000`
- Frontend dev server: `npm --prefix web run dev` (http://localhost:5173)
- Build the pages: `npm --prefix web run build`
- Look inside an `.ld` file: `.venv\Scripts\python backend\scripts\inspect_ld.py "path\to\file.ld"`

## Rules
- **After any change in `web/`**, run `npm --prefix web run build` and commit the built `backend/fs_telemetry/web/`
  in the same commit. `test_web_build.py` fails otherwise.
- **Never touch the user's real data** in tests or experiments: the telemetry folders and the app's data folder
  (`FS Telemetry\` in the user's home, `FS_DATA_DIR`). Use `tmp_path` and point settings at it, like `conftest.py` does.
- **Never commit telemetry, setups or personal files**: no `.ld`/`.ibt`/`.csv` runs, no `my-settings.bat`, no absolute
  paths containing a user name, no e-mail addresses. Write paths as `C:\Users\<you>\...` or by their setting name.
- Dependencies are pinned (`backend/requirements.lock.txt`, `web/package-lock.json`); change them only via `pip`/`npm`.
- Keep branches short and based on `main`; one topic per branch, no stacking.
- Commit messages: a short imperative subject line saying what changed for the user (see `git log`). No AI
  attribution lines ("Co-Authored-By", "Generated with ...").
- Don't push, merge or open a pull request unless the person you're working for asks you to.

## Conventions and gotchas
- Every source is converted to Assetto Corsa's channel names and units, so layouts and maths work for all sims.
- Laps are compared by **distance**, not time. All maths assumes evenly spaced samples.
- Estimated channels are labelled "(est.)" and carry a hover note on how far to trust them. Their accuracy is
  scored by the backtests against `backend/fs_telemetry/backtest/baseline.json`; a change that makes one worse fails.
- iRacing has no slip angles, tyre loads or track edges; balance there comes from steering, and slip ratios are estimated.
- Car facts (steering ratio, wheelbase) come from `cars.py` first, then the car's AC files, then are learned from the
  run and labelled as learned.
- User data (layouts, setup library, notes, CSV column mappings) is JSON in `FS_DATA_DIR`; any Markdown/CSV copies
  are generated from it and never read back.
- Folder settings come from environment variables (`FS_TELEMETRY_DIR`, `FS_CSP_DIR`, `FS_IRACING_DIR`, `FS_CSV_DIR`,
  `FS_AC_DIR`, `FS_AC_SETUPS_DIR`, `FS_DATA_DIR`, `FS_SHARED_DIR`, `FS_SHARED_NAME`), set per PC in `my-settings.bat`.
- Team library (`shared.py`): a folder the whole team syncs (e.g. Teams/SharePoint via OneDrive), one subfolder per
  person that only they write in, so OneDrive never makes conflict copies. Each shared run has a `.summary.json`
  next to it; the run list reads those so OneDrive doesn't download every run just to list it. Teammates' run ids
  are `<name>~<file>`.
- Synced folders are deep and AC/CSP file names long: open files through `shared.long_path` where a path can pass
  Windows' 260-character limit.
- Loggers don't all name a track the same way: one CSP writes the track folder (`sportsland_sugo_mts`), another the
  folder with the layout stuck on the end (`sportsland_sugo_mtssugo`). `ac_track.canonical_track` turns AC names into
  the installed folder's name (or a hand-made alias from `track_aliases.json` in `FS_DATA_DIR`) when a run is read,
  and for teammates' saved summaries. `session_key` still hashes the name as logged (`logged_track`), so notes keys
  never change.
- UI text explains itself in hover text rather than in the README; keep new features the same way.

## Keep this file current
This file is the shared context for every agent and contributor, so update it as part of the change that makes it
out of date: a new module or page, a changed data flow, a new rule, or a gotcha that cost you time. Keep it short,
say *why* where it isn't obvious, and keep it free of personal information (names, paths, machines, accounts).
