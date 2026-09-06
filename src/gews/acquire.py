"""
Data acquisition module — search and download Sentinel-1 SLC scenes
from the Alaska Satellite Facility (ASF) DAAC archive.

Usage:
    from gews.acquire import search_scenes, select_track, download_scenes

    scenes = search_scenes(config)
    track_scenes = select_track(scenes)
    download_scenes(track_scenes, output_dir="data/slc")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import asf_search as asf
from shapely.geometry import Point

logger = logging.getLogger(__name__)


@dataclass
class SceneInfo:
    """Metadata for a single Sentinel-1 SLC scene."""

    granule: str
    start_time: str
    path_number: int
    frame_number: int
    polarization: str
    url: str
    file_size_mb: float
    geometry: dict  # GeoJSON geometry

    @classmethod
    def from_asf_result(cls, result: asf.ASFProduct) -> SceneInfo:
        props = result.properties
        return cls(
            granule=props["fileID"],
            start_time=props["startTime"],
            path_number=props["pathNumber"],
            frame_number=props["frameNumber"],
            polarization=props.get("polarization", "VV"),
            url=props["url"],
            file_size_mb=props.get("bytes", 0) / 1e6,
            geometry=result.geometry,
        )


def search_scenes(config: dict) -> list[SceneInfo]:
    """
    Search ASF archive for Sentinel-1 SLC scenes covering the study area.

    Parameters
    ----------
    config : dict
        Parsed YAML configuration with 'site' and 'acquire' sections.

    Returns
    -------
    list[SceneInfo]
        Scenes matching the search criteria, sorted by acquisition date.
    """
    site = config["site"]
    acq = config["acquire"]

    center = Point(site["longitude"], site["latitude"])
    # Buffer in degrees (approximate: 1° ≈ 111 km)
    buffer_deg = site.get("buffer_km", 15) / 111.0
    aoi = center.buffer(buffer_deg)

    logger.info(
        "Searching ASF for Sentinel-1 SLC scenes: "
        "%.2f°N, %.2f°E ± %d km, %s to %s",
        site["latitude"],
        site["longitude"],
        site.get("buffer_km", 15),
        acq["start_date"],
        acq["end_date"],
    )

    search_kwargs = dict(
        platform=asf.PLATFORM.SENTINEL1,
        processingLevel=asf.PRODUCT_TYPE.SLC,
        beamMode=asf.BEAMMODE.IW,
        intersectsWith=aoi.wkt,
        start=acq["start_date"],
        end=acq["end_date"],
    )

    if acq.get("path_number"):
        search_kwargs["relativeOrbit"] = [acq["path_number"]]

    results = asf.search(**search_kwargs)
    scenes = [SceneInfo.from_asf_result(r) for r in results]
    scenes.sort(key=lambda s: s.start_time)

    max_scenes = acq.get("max_scenes", 200)
    if len(scenes) > max_scenes:
        logger.warning(
            "Found %d scenes, limiting to %d most recent", len(scenes), max_scenes
        )
        scenes = scenes[-max_scenes:]

    logger.info("Found %d scenes across %d orbital tracks", len(scenes), _count_tracks(scenes))
    return scenes


def _count_tracks(scenes: list[SceneInfo]) -> int:
    return len({s.path_number for s in scenes})


def select_track(
    scenes: list[SceneInfo], path_number: int | None = None
) -> list[SceneInfo]:
    """
    Select scenes from a single orbital track for consistent viewing geometry.

    If path_number is None, selects the track with the most scenes
    (maximizing temporal sampling).

    Parameters
    ----------
    scenes : list[SceneInfo]
        All scenes from search.
    path_number : int or None
        Specific track to select, or None for auto-selection.

    Returns
    -------
    list[SceneInfo]
        Scenes from the selected track.
    """
    tracks: dict[int, list[SceneInfo]] = {}
    for s in scenes:
        tracks.setdefault(s.path_number, []).append(s)

    if path_number is not None:
        if path_number not in tracks:
            available = sorted(tracks.keys())
            raise ValueError(
                f"Track {path_number} not found. Available: {available}"
            )
        selected = tracks[path_number]
    else:
        # Pick track with most scenes
        path_number = max(tracks, key=lambda k: len(tracks[k]))
        selected = tracks[path_number]

    logger.info(
        "Selected track %d: %d scenes, %s to %s",
        path_number,
        len(selected),
        selected[0].start_time[:10],
        selected[-1].start_time[:10],
    )

    # Report other tracks for reference
    for p, s in sorted(tracks.items()):
        if p != path_number:
            logger.info("  Skipped track %d: %d scenes", p, len(s))

    return selected


def download_scenes(
    scenes: list[SceneInfo],
    output_dir: str | Path = "data/slc",
    n_workers: int = 4,
    username: str | None = None,
    password: str | None = None,
) -> list[Path]:
    """
    Download SLC scenes from ASF.

    Requires an Earthdata Login account. Credentials can be provided
    directly or via ~/.netrc file (recommended).

    Parameters
    ----------
    scenes : list[SceneInfo]
        Scenes to download.
    output_dir : str or Path
        Directory to save downloaded files.
    n_workers : int
        Number of parallel download threads.
    username, password : str or None
        Earthdata Login credentials. If None, reads from ~/.netrc.

    Returns
    -------
    list[Path]
        Paths to downloaded files.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Check for already-downloaded files
    existing = {p.stem for p in output_dir.glob("*.zip")}
    to_download = [s for s in scenes if s.granule not in existing]

    if len(to_download) < len(scenes):
        logger.info(
            "%d of %d scenes already downloaded, downloading %d remaining",
            len(scenes) - len(to_download),
            len(scenes),
            len(to_download),
        )

    if not to_download:
        logger.info("All scenes already downloaded")
        return list(output_dir.glob("*.zip"))

    total_gb = sum(s.file_size_mb for s in to_download) / 1024
    logger.info(
        "Downloading %d scenes (%.1f GB) with %d workers",
        len(to_download),
        total_gb,
        n_workers,
    )

    # Authenticate
    if username and password:
        session = asf.ASFSession().auth_with_creds(username, password)
    else:
        # Tries ~/.netrc, then environment variables
        session = asf.ASFSession()
        try:
            session.auth_with_creds(
                __import__("os").environ.get("EARTHDATA_USER", ""),
                __import__("os").environ.get("EARTHDATA_PASS", ""),
            )
        except Exception:
            logger.warning(
                "No credentials found. Set EARTHDATA_USER/EARTHDATA_PASS "
                "or configure ~/.netrc for urs.earthdata.nasa.gov"
            )
            raise

    # Download using ASF's built-in downloader
    urls = [s.url for s in to_download]
    asf.download_urls(
        urls=urls,
        path=str(output_dir),
        session=session,
        processes=n_workers,
    )

    downloaded = list(output_dir.glob("*.zip"))
    logger.info("Download complete: %d files in %s", len(downloaded), output_dir)
    return downloaded


def summarize_scenes(scenes: list[SceneInfo]) -> str:
    """Return a human-readable summary of a scene collection."""
    if not scenes:
        return "No scenes found."

    tracks = {}
    for s in scenes:
        tracks.setdefault(s.path_number, []).append(s)

    lines = [
        f"Total: {len(scenes)} scenes, "
        f"{len(tracks)} track(s), "
        f"{scenes[0].start_time[:10]} to {scenes[-1].start_time[:10]}",
        "",
    ]
    for path, track_scenes in sorted(tracks.items()):
        interval = "—"
        if len(track_scenes) > 1:
            from datetime import datetime

            dates = [
                datetime.fromisoformat(s.start_time.replace("Z", "+00:00"))
                for s in track_scenes
            ]
            gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
            interval = f"median {sorted(gaps)[len(gaps)//2]}d"

        lines.append(
            f"  Track {path:>3d}: {len(track_scenes):>3d} scenes, "
            f"{track_scenes[0].start_time[:10]} → {track_scenes[-1].start_time[:10]}, "
            f"revisit {interval}"
        )

    total_gb = sum(s.file_size_mb for s in scenes) / 1024
    lines.append(f"\nTotal download: {total_gb:.1f} GB")
    return "\n".join(lines)
