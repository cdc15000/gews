#!/usr/bin/env python3
"""Download NISAR GUNW and GOFF track 98 only, for the collapse study period."""
import asf_search as asf
import netrc
from pathlib import Path

n = netrc.netrc()
auth = n.authenticators("urs.earthdata.nasa.gov")
session = asf.ASFSession()
session.auth_with_creds(auth[0], auth[2])

SITE = "POINT(85.90 28.20)"

# --- GUNW: track 98 only ---
gunw = asf.search(platform="NISAR", processingLevel="GUNW",
    intersectsWith=SITE, start="2025-01-01", end="2026-09-01", maxResults=200)

gunw_dir = Path("notebooks/data/nisar/gunw")
gunw_dir.mkdir(parents=True, exist_ok=True)
existing = {f.name for f in gunw_dir.iterdir()}

n_gunw = 0
for r in sorted(gunw, key=lambda x: x.properties["startTime"]):
    fname = r.properties["fileName"]
    if "_098_" not in fname:
        continue
    n_gunw += 1
    if fname in existing:
        print(f"SKIP GUNW: {fname[:70]}", flush=True)
        continue
    print(f"GUNW: {fname[:70]}...", flush=True)
    r.download(gunw_dir, session=session)
print(f"GUNW track 98 total found: {n_gunw}", flush=True)

# --- GOFF: track 98 only ---
goff = asf.search(platform="NISAR", processingLevel="GOFF",
    intersectsWith=SITE, start="2025-01-01", end="2026-09-01", maxResults=200)

goff_dir = Path("notebooks/data/nisar/goff")
goff_dir.mkdir(parents=True, exist_ok=True)
existing_goff = {f.name for f in goff_dir.iterdir()}

n_goff = 0
for r in sorted(goff, key=lambda x: x.properties["startTime"]):
    fname = r.properties["fileName"]
    if "_098_" not in fname:
        continue
    n_goff += 1
    if fname in existing_goff:
        print(f"SKIP GOFF: {fname[:70]}", flush=True)
        continue
    print(f"GOFF: {fname[:70]}...", flush=True)
    r.download(goff_dir, session=session)
print(f"GOFF track 98 total found: {n_goff}", flush=True)

print("ALL_DOWNLOADS_COMPLETE", flush=True)
