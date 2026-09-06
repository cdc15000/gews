#!/usr/bin/env python3
"""Download NISAR GUNW (tracks 48,98) and GOFF for the collapse study period."""
import asf_search as asf
import netrc
from pathlib import Path

n = netrc.netrc()
auth = n.authenticators("urs.earthdata.nasa.gov")
session = asf.ASFSession()
session.auth_with_creds(auth[0], auth[2])

SITE = "POINT(85.90 28.20)"

# --- GUNW: tracks 48 and 98 only (skip track 170) ---
gunw = asf.search(platform="NISAR", processingLevel="GUNW",
    intersectsWith=SITE, start="2025-01-01", end="2026-08-26", maxResults=200)

gunw_dir = Path("notebooks/data/nisar/gunw")
gunw_dir.mkdir(parents=True, exist_ok=True)
existing = {f.name for f in gunw_dir.iterdir()}

for r in sorted(gunw, key=lambda x: x.properties["startTime"]):
    fname = r.properties["fileName"]
    # Only tracks 048 and 098
    if "_048_" not in fname and "_098_" not in fname:
        continue
    if fname in existing:
        print(f"SKIP GUNW: {fname[:60]}", flush=True)
        continue
    print(f"GUNW: {fname[:60]}...", flush=True)
    r.download(gunw_dir, session=session)

# --- GOFF: all tracks for pre-collapse period ---
goff = asf.search(platform="NISAR", processingLevel="GOFF",
    intersectsWith=SITE, start="2025-01-01", end="2026-08-26", maxResults=200)

goff_dir = Path("notebooks/data/nisar/goff")
goff_dir.mkdir(parents=True, exist_ok=True)
existing_goff = {f.name for f in goff_dir.iterdir()}

for r in sorted(goff, key=lambda x: x.properties["startTime"]):
    fname = r.properties["fileName"]
    # Only tracks 048 and 098
    if "_048_" not in fname and "_098_" not in fname:
        continue
    if fname in existing_goff:
        print(f"SKIP GOFF: {fname[:60]}", flush=True)
        continue
    print(f"GOFF: {fname[:60]}...", flush=True)
    r.download(goff_dir, session=session)

print("ALL_DOWNLOADS_COMPLETE", flush=True)
