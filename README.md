# FS Sim Telemetry Viewer

A web dashboard for the SUFST FS sim team to analyse driving data from **Assetto Corsa** (MoTeC `.ld` files from the
**telemetrick** app or Custom Shaders Patch's own telemetry logger), **iRacing** (`.ibt` files) and **CSV** files from
other sims or loggers.
It runs on your own PC; nothing is uploaded anywhere.

| Page | What it's for |
|---|---|
| **Telemetry** | Lap-vs-lap traces over distance, with a readout of every value under the cursor. Overlay, recolour and save chart layouts. |
| **Stint** | A whole run at a glance: lap times, fuel, tyre wear, KERS. *Customise* picks the tiles (remembered per car). |
| **Metrics** | Each lap split into braking / low / mid / high speed / power-limited sections, balance per section, and damper histograms. |
| **Track** | Racing lines on the track map (by speed, lap or pedal), mini-sector dominance, theoretical best lap, CSV download. |
| **Setups** | A setup library: import from AC's setups folder or iRacing runs, compare versions side by side. |

Hover a channel or number in the app to see what it is and how far to trust it. Channels marked **(est.)** are estimates.

---

## Getting started

You need **Windows**. Commands go in **PowerShell** (Windows key, type `PowerShell`, Enter); right-click to paste.

1. **Install Git and Python 3.14**, one command at a time (say **Yes** / type `Y` if asked):
   ```powershell
   winget install --id Git.Git -e
   ```
   ```powershell
   winget install --id Python.Python.3.14 -e
   ```
   Then **open a new PowerShell** and check both answer with a version number: `git --version; py -3.14 --version`
2. **Download the code** into your Documents:
   ```powershell
   cd $HOME\Documents; git clone https://github.com/Woolieryapper1/Simtelemetryviewer.git
   ```
3. **Set it up:** in File Explorer, open **Documents > Simtelemetryviewer** and double-click **`setup.bat`**.
   Wait for **"All set"**. It installs everything into `.venv\` in that folder, nothing system-wide.
4. **Start it:** double-click **`start.bat`**. Leave the black window open (it *is* the app); your browser opens
   **http://127.0.0.1:8000**. Close the window to stop. Next time, just `start.bat`.

**Updating:** in PowerShell, `cd $HOME\Documents\Simtelemetryviewer; git pull`, then run `setup.bat` again.

## Where it looks for files

The black window lists the folders it's using. The defaults ("Documents" is found even if OneDrive backs it up):

| What | Default folder | Setting |
|---|---|---|
| AC telemetry (`.ld` / `.ldx`) | `Documents\Assetto Corsa\apps\telemetrick\exported` | `FS_TELEMETRY_DIR` |
| AC telemetry from CSP's logger | `Documents\Assetto Corsa\telemetry` | `FS_CSP_DIR` |
| iRacing telemetry (`.ibt`) | `Documents\iRacing\telemetry` | `FS_IRACING_DIR` |
| CSV telemetry | `FS Telemetry\csv` | `FS_CSV_DIR` |
| AC install (track outlines) | `C:\Program Files (x86)\Steam\steamapps\common\assettocorsa` | `FS_AC_DIR` |
| AC setups | `Documents\Assetto Corsa\setups` | `FS_AC_SETUPS_DIR` |
| The app's saved data | `C:\Users\<you>\FS Telemetry` | `FS_DATA_DIR` |

Runs from telemetrick and from CSP's own logger both show up. CSP doesn't log the car's
wheelbase, so Understeer Angle only shows for its runs if the car's steering ratio or wheelbase is known (`cars.py`
or the car's unpacked AC files).

To change one, copy **`my-settings.example.bat`** to **`my-settings.bat`**, fill it in (instructions inside) and restart.
Sub-folders are searched too.

**FS Telemetry** holds your layouts, setup library, run notes and CSV column mappings. It's outside the app folder,
so updates never touch it. Colours and page layouts are remembered in your browser.

## Good to know

- **iRacing:** turn logging on in the car with **Alt+L**. Channels are renamed to AC's names and units, so layouts work
  for both. No track edges (iRacing's track files are encrypted), and no tyre loads, so balance comes from steering
  (tagged *known* or *learned*; add a car to `backend/fs_telemetry/cars.py` to use known numbers).
- **CSV:** most layouts just work (commas/semicolons/tabs, decimal commas, units rows, MoTeC i2 exports). The first
  time it sees a new set of columns it writes its guess to `FS Telemetry\csv_mappings.json`; edit that in Notepad
  to fix a column's channel or unit, then refresh.
- **Track outlines** come from AC's track files. For a track you don't have installed, copy its folder into
  `FS Telemetry\tracks\`.
- **Notes, tags and stars:** use the *Notes* box in the run list; ✎ next to a lap marks it *use* or *ignore* for the
  Stint averages. *Export* / *Import notes* shares them between PCs.
- Laps line up by **distance**, not time. Out-laps, incomplete and invalid laps are labelled and left out of averages.

## Something went wrong?

| What you see | What to do |
|---|---|
| `winget` is *not recognized* | Download [Git](https://git-scm.com/downloads/win) and [Python 3.14](https://www.python.org/downloads/) from the websites (tick **Add python.exe to PATH**). |
| `git` or `py` is *not recognized* | Open a new PowerShell. If it persists, install that tool again (step 1). |
| setup.bat: **Python 3.14 wasn't found** | Install Python 3.14 (step 1). Other versions won't do. |
| No sessions show up | Check the folders above, or set them in `my-settings.bat`. |
| A CSV file doesn't show up | The black window says why; often a column needs naming in `csv_mappings.json`. |
| *"No AC track files"* on the map | Copy the track's folder into `FS Telemetry\tracks\`. |
| *"Address already in use"* | The app is already running in another window. Use it, or run `start.bat --port 8001`. |
| *"This site can't be reached"* | Wait a few seconds and refresh; check the black window is still open. |
| Red error text in the black window | Screenshot it and send it to Nicolas. |

---

## For developers

It's a Python package (FastAPI backend in `backend/fs_telemetry/`) with a React + Vite frontend in `web/`.
The built pages ship inside the package (`backend/fs_telemetry/web/`, don't edit by hand), so users don't need Node.
API docs with "Try it out" are at http://127.0.0.1:8000/docs.

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python -m pip install -r backend\requirements.lock.txt
.venv\Scripts\python -m pip install --no-deps -e ".[dev]"
npm --prefix web ci
```

Run the backend and the Vite dev server in two windows, then open http://localhost:5173:

```powershell
.venv\Scripts\python -m uvicorn fs_telemetry.main:app --reload --port 8000
npm --prefix web run dev
```

- **After changing `web/`**, run `npm --prefix web run build` and commit the result (`test_web_build.py` checks).
- **Tests:** `.venv\Scripts\python -m pytest`
- **Look inside an `.ld` file:** `.venv\Scripts\python backend\scripts\inspect_ld.py "path\to\file.ld"`
- **Dependencies** are pinned in `backend/requirements.lock.txt` and `web/package-lock.json`; change them only via `pip` / `npm`.

### Backtests

Estimated channels (aero, steering-based balance) are scored against a corpus of your own telemetry plus synthetic
runs with known answers. `backend/fs_telemetry/backtest/baseline.json` holds the accepted numbers; a change that makes
one worse than its tolerance fails.

```powershell
.venv\Scripts\fs-telemetry validate                    # report in backtest-report (add --html for a web page)
.venv\Scripts\python -m pytest -m backtest             # the same, as a test
.venv\Scripts\fs-telemetry validate --update-baseline  # accept a deliberate improvement, then commit baseline.json
```

`backend/tests/backtest/corpus.json` says which folders and runs to use (telemetry files never go into git).
To add an estimator, copy `backtest/adapters/aero_fit.py` (`measure`, `summarise`, `checks`, `sources`) and list it
in `adapters/__init__.py`. The balance hover text on the Metrics page is read from `baseline.json`, so re-run
`validate --only balance --update-baseline` after changing `metrics.py` or `maths.py`.

Known aero limit: ClA reads 4-16 % higher when fitted on the faster half of a run than the slower half (real aero
changes with ride height), so quote it as an average over the run.
