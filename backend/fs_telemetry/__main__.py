"""`python -m fs_telemetry`: same as the fs-telemetry command.

start.bat uses this because some PCs' Windows Application Control blocks the fs-telemetry.exe launcher
that pip makes, while python.exe itself is allowed.
"""
from .cli import main

main()
