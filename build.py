#!/usr/bin/env python3
"""Pack the Log Collector and Serrebi Fixes add-on into a .nvda-addon under dist/."""

import io
import os
import re
import zipfile

NAME = "logCollectorAndFixesFromSerrebi"
DIST_DIR = "dist"

INCLUDE = ("manifest.ini", "readme.html", "globalPlugins")


def readManifestVersion(path="manifest.ini"):
    """-> the version manifest.ini declares.

    Read rather than repeated as a constant here: a hardcoded version silently
    packs the new code under the old filename the moment the two drift apart,
    and the add-on that gets installed is then named for a release it is not.
    """
    text = io.open(path, encoding="utf-8").read()
    match = re.search(r"^\s*version\s*=\s*(.+?)\s*$", text, re.M)
    if not match:
        raise SystemExit("no version in %s" % path)
    return match.group(1).strip().strip('"')


def build():
    version = readManifestVersion()
    os.makedirs(DIST_DIR, exist_ok=True)
    out = os.path.join(DIST_DIR, "%s-%s.nvda-addon" % (NAME, version))

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for item in INCLUDE:
            if os.path.isfile(item):
                z.write(item, item)
            elif os.path.isdir(item):
                for root, dirs, files in os.walk(item):
                    # Bytecode caches are rebuilt by whichever NVDA installs
                    # this, and shipping one built by another Python version
                    # is worse than shipping none.
                    dirs[:] = [d for d in dirs if d != "__pycache__"]
                    for f in files:
                        path = os.path.join(root, f)
                        z.write(path, os.path.relpath(path, "."))

    print("built %s" % out)
    return out


if __name__ == "__main__":
    build()
