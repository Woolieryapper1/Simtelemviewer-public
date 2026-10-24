"""The fs-telemetry command: starts the app and opens it in the browser.

    fs-telemetry                  start on http://127.0.0.1:8000 and open the browser
    fs-telemetry --port 8001      use another port (if 8000 is taken)
    fs-telemetry --no-browser     don't open the browser
    fs-telemetry validate         check how good the estimates are (see backtest/validate.py)

pyproject.toml turns main() into the fs-telemetry command when the package is installed.
"""

from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn

from . import config
from .shared import folder_name


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="fs-telemetry", description="FS sim telemetry viewer")
    parser.add_argument("--port", type=int, default=8000, help="port to run on (default 8000)")
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser")
    commands = parser.add_subparsers(dest="command", title="other commands")
    from .backtest import validate  # the backtest: fs-telemetry validate

    validate.add_arguments(commands.add_parser(
        "validate", help="backtest the estimated channels (aero, ...) and check them against the baseline",
        description=validate.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter))
    args = parser.parse_args(argv)
    if args.command == "validate":
        raise SystemExit(validate.run_from_args(args))

    url = f"http://127.0.0.1:{args.port}"
    found = lambda folder: "" if folder.exists() else "  (not found)"  # noqa: E731
    team = (f"{config.SHARED_DIR}{found(config.SHARED_DIR)}  (you're {folder_name(config.SHARED_NAME)})"
            if config.SHARED_DIR else "off (set FS_SHARED_DIR to share runs with the team)")
    # Say where everything is read from and saved to, so it's easy to check and find
    print(f"""
  FS Telemetry is starting at {url}
  Keep this window open while you use it; close it (or press Ctrl+C) to stop.

  AC telemetry:     {config.TELEMETRY_DIR}{found(config.TELEMETRY_DIR)}
  iRacing (.ibt):   {config.IRACING_DIR}{found(config.IRACING_DIR)}
  CSV telemetry:    {config.CSV_DIR}{found(config.CSV_DIR)}
  AC install:       {config.AC_DIR}
  AC setups:        {config.AC_SETUPS_DIR}
  Saved data in:    {config.DATA_DIR}  (put extra track folders in its tracks folder)
  Team library:     {team}
""", flush=True)
    if not config.TELEMETRY_DIR.exists() and not config.IRACING_DIR.exists():
        print("  ! Neither telemetry folder exists - set FS_TELEMETRY_DIR or FS_IRACING_DIR "
              "(see my-settings.example.bat).\n", flush=True)
    if not args.no_browser:
        # Open the browser once the server has had a moment to start
        threading.Timer(1.5, webbrowser.open, [url]).start()
    # Only this PC can reach it (127.0.0.1), not the rest of the network
    uvicorn.run("fs_telemetry.main:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
