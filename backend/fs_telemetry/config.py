"""Settings. Change these with environment variables rather than editing code,
so each team member can point the app at their own telemetry folder."""

import getpass
import os
from pathlib import Path


def documents_folder() -> Path:
    """The user's real Documents folder.

    Windows lets Documents move - most often into OneDrive, when OneDrive backs it
    up - and programs like iRacing save into wherever it is now. So ask Windows
    (it keeps the location in the registry) rather than guess. Elsewhere, or if
    that fails: the usual Documents in the user folder."""
    try:
        import winreg

        key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            value, _ = winreg.QueryValueEx(k, "Personal")  # "Personal" is Windows' old name for Documents
        return Path(os.path.expandvars(value))  # e.g. %USERPROFILE%\OneDrive\Documents
    except (ImportError, OSError):
        return Path.home() / "Documents"


def first_existing(*candidates: Path) -> Path:
    """The first folder that exists, or the first one if none do (so the start-up
    window shows where it looked)."""
    return next((p for p in candidates if p.exists()), candidates[0])


def in_documents(*parts: str, setting: str) -> Path:
    """A folder inside Documents (wherever Windows keeps it), unless the setting
    (an environment variable, e.g. from my-settings.bat) says otherwise."""
    if os.environ.get(setting):
        return Path(os.environ[setting])
    return first_existing(
        documents_folder().joinpath(*parts),
        Path.home().joinpath("OneDrive", "Documents", *parts),  # in case Windows' answer is out of date
        Path.home().joinpath("Documents", *parts),
    )


# Folder with AC's MoTeC .ld/.ldx files. By default where the telemetrick app exports
# them; Custom Shaders Patch's own logger saves into Assetto Corsa/telemetry instead,
# so point this at the Assetto Corsa folder to get both.
# Searched recursively, so <driver>/<car>/<track>/ sub-folders are fine.
TELEMETRY_DIR = in_documents("Assetto Corsa", "apps", "telemetrick", "exported", setting="FS_TELEMETRY_DIR")

# Folder iRacing saves its .ibt telemetry files into (searched recursively too):
# iRacing\telemetry in Documents, wherever Documents is on this PC.
# iRacing only writes them if telemetry logging is on (Alt+L in the car, or always-on in app.ini).
IRACING_DIR = in_documents("iRacing", "telemetry", setting="FS_IRACING_DIR")

# Where to look for AC track folders (for the track outline on the map), in order.
# 1. tracks in the data folder (DATA_DIR, below) - copy a track folder here if it isn't in your AC install
# 2. the AC install's content/tracks
AC_DIR = Path(os.environ.get("FS_AC_DIR", "C:/Program Files (x86)/Steam/steamapps/common/assettocorsa"))

# AC's saved setups (Documents/Assetto Corsa/setups/<car>/<track>/<name>.ini), for importing
AC_SETUPS_DIR = in_documents("Assetto Corsa", "setups", setting="FS_AC_SETUPS_DIR")

# Where the app saves things it creates (chart layouts, the setup library) and where
# extra track folders go (tracks/). A normal folder in your user folder, so it's easy
# to find and isn't lost when the app is reinstalled or updated.
DATA_DIR = Path(os.environ.get("FS_DATA_DIR", Path.home() / "FS Telemetry"))

# Folder for telemetry as CSV files (from other sims, data loggers, or the app's own
# CSV downloads), searched recursively. How each source's columns are read is saved
# in csv_mappings.json in DATA_DIR (see ingest/csv_import.py).
CSV_DIR = Path(os.environ.get("FS_CSV_DIR", DATA_DIR / "csv"))

# The team library (see shared.py): a folder everyone on the team syncs, e.g. a folder
# in the SUFST Teams/SharePoint site synced to this PC by OneDrive. Off unless set.
SHARED_DIR = Path(os.environ["FS_SHARED_DIR"]) if os.environ.get("FS_SHARED_DIR") else None

# Your name in the team library: the folder your shared runs go into. Your Windows
# user name unless set, so set it if that isn't what the team knows you by.
SHARED_NAME = os.environ.get("FS_SHARED_NAME") or getpass.getuser()

TRACKS_DIRS = [DATA_DIR / "tracks", AC_DIR / "content" / "tracks"]

# Where to look for AC car folders (for the steering ratio and wheelbase), in order.
# Only cars whose data/ folder isn't packed into data.acd can be read.
CARS_DIRS = [DATA_DIR / "cars", AC_DIR / "content" / "cars"]
