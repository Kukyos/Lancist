"""
Resolve the on-disk data directory.

When packaged as a one-file PyInstaller exe, __file__ points inside a temp
extraction folder that's wiped between runs. We want leads.json / emails.csv
/ config.json to live next to the .exe, so the user can find them.
"""
import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    APP_DIR = Path(os.path.dirname(sys.executable))
else:
    APP_DIR = Path(__file__).parent

LEADS_FILE   = APP_DIR / "leads.json"
EMAILS_CSV   = APP_DIR / "emails.csv"
CONFIG_FILE  = APP_DIR / "config.json"
