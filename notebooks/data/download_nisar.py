#!/usr/bin/env python3
"""Download remaining NISAR GUNW and GOFF products."""
import asf_search as asf
import netrc
from pathlib import Path

n = netrc.netrc()
auth = n.authenticators("urs.earthdata.nasa.gov")
session = asf.ASFSession()
session.auth_with_creds(auth[0], auth[2])

SITE_LAT = 28.20
SITE_LON = 85.90

# Download remaining GUNW
gunw = asf.search(platform="NISAR", processingLevel="GUNW",
    intersectsWith=f"POINT({SITE_LON} {SITE_LAT})", start="2025-01-01",
    end="2026-08-26", maxResults=200)

gunw_dir = Path("notebooks/data/nisar/gunw")
gunw_dir.mkdir(parents=True, exist_ok=True)
existing = {f.name for f in gunw_dir.iterdir()}

for i, r in enumerate(sorted(gunw, key=lambda x: x.properties["startTime"])):
    fname = r.properties["fileName"]
    if fname in existing:
        continue
    print(f"GUNW {i+1}/{len(gunw)}: {fname[:60]}...", flush=True)
    r.download(gunw_dir, session=session)

# Download GOFF
goff = asf.search(platform="NISAR", processingLevel="GOFF",
    intersectsWith=f"POINT({SITE_LON} {SITE_LAT})", start="2025-01-01",
    end="2026-08-26", maxResults=200)

goff_dir = Path("notebooks/data/nisar/goff")
goff_dir.mkdir(parents=True, exist_ok=True)
existing_goff = {f.name for f in goff_dir.iterdir()} if goff_dir.exists() else set()

for i, r in enumerate(sorted(goff, key=lambda x: x.properties["startTime"])):
    fname = r.properties["fileName"]
    if fname in existing_goff:
        continue
    print(f"GOFF {i+1}/{len(goff)}: {fname[:60]}...", flush=True)
    r.download(goff_dir, session=session)

print("ALL_NISAR_DOWNLOADS_COMPLETE", flush=True)
