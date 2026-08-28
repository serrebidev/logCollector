#!/usr/bin/env python3
"""Pack the Log Collector and Serrebi Fixes add-on into a .nvda-addon under dist/."""

import os
import zipfile

NAME = "logCollectorAndFixesFromSerrebi"
VERSION = "2026.8.19.1"
DIST_DIR = "dist"

INCLUDE = ("manifest.ini", "readme.html", "globalPlugins")

os.makedirs(DIST_DIR, exist_ok=True)
out = os.path.join(DIST_DIR, f"{NAME}-{VERSION}.nvda-addon")

with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for item in INCLUDE:
        if os.path.isfile(item):
            z.write(item, item)
        elif os.path.isdir(item):
            for root, _dirs, files in os.walk(item):
                for f in files:
                    path = os.path.join(root, f)
                    z.write(path, os.path.relpath(path, "."))

print(f"built {out}")
