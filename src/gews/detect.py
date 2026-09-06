"""
Anomaly detection module — Tier 0 screening for unstable glaciers
and rock slopes.

Takes acceleration maps from the timeseries module and identifies
spatially coherent clusters of anomalous acceleration. Each cluster
is a "flag" — a candidate unstable site that advances to Tier 1
filtering.

Design principles:
    - Each site is compared to its own history (unsupervised)
    - No labeled collapse data required
    - Spatial coherence filters isolated noisy pixels
    - Output is a ranked list of flags with anomaly scores
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage
from sklearn.cluster import DBSCAN

from gews.timeseries import AccelerationMap, detect_step_changes, fit_voight

logger = logging.getLogger(__name__)


@dataclass
class AnomalyFlag:
    """
    A spatially coherent cluster of anomalous acceleration.

    This is the output of Tier 0 screening — each flag represents
    a candidate unstable site that should advance to Tier 1 filtering.
    """

    flag_id: int
    score: float                    # composite anomaly score (higher = more anomalous)
    peak_zscore: float              # maximum z-score in the cluster
    mean_zscore: float              # mean z-score across cluster pixels
    n_pixels: int                   # number of pixels in cluster
    area_m2: float                  # approximate area of deforming zone
    center_lat: float
    center_lon: float
    peak_lat: float                 # location of maximum anomaly
    peak_lon: float
    acceleration_m_yr2: float       # peak acceleration in m/yr²
    pixel_indices: np.ndarray       # [n_pixels, 2] row/col indices
    window_index: int               # time window where anomaly peaks
    window_date: float              # ordinal date of that window center

    # Voight analysis (if applicable)
    voight_fit: dict | None = None  # predicted failure time, R², etc.

    # Metadata for Tier 1
    detection_details: dict = field(default_factory=dict)


def detect_anomalies(
    accel_map: AccelerationMap,
    latitude: np.ndarray,
    longitude: np.ndarray,
    config: dict,
    dates: np.ndarray | None = None,
    displacement: np.ndarray | None = None,
) -> list[AnomalyFlag]:
    """
    Run Tier 0 anomaly detection on an acceleration map.

    Algorithm:
    1. Threshold the z-score map at each time window
    2. Cluster spatially connected anomalous pixels (DBSCAN)
    3. Filter clusters by minimum size
    4. Score and rank surviving clusters
    5. If raw displacement is supplied, also run step-change detection
       and merge (union) its flags with the acceleration-based flags
    6. Optionally fit Voight's law to top candidates

    The acceleration detector catches gradual pre-failure speed-up
    (Voight-style). It misses failures like the Nepal 2026 collapse,
    which produced 3.5 m of displacement in a single 12-day cycle
    with velocities that *decelerated* after the initial rupture —
    no gradual acceleration trend for Voight's law to fit. Supplying
    `dates`/`displacement` enables `_detect_step_change_anomalies()`
    to catch that pattern directly, independent of any trend model.

    Parameters
    ----------
    accel_map : AccelerationMap
        Output from timeseries.compute_acceleration_map().
    latitude, longitude : np.ndarray
        Coordinate grids, shape [n_rows, n_cols].
    config : dict
        Detection configuration (from config YAML 'detect' section).
    dates : np.ndarray, optional
        Acquisition dates as ordinal days, shape [n_epochs]. Required
        (together with `displacement`) to enable step-change detection.
    displacement : np.ndarray, optional
        Raw LOS displacement in meters, shape [n_epochs, n_rows, n_cols].
        Required (together with `dates`) to enable step-change detection.

    Returns
    -------
    list[AnomalyFlag]
        Flags sorted by score (highest first).
    """
    det = config["detect"]
    sigma = det["acceleration"]["sigma_threshold"]
    clust = det["clustering"]
    min_pixels = clust["min_cluster_pixels"]
    min_area = clust["min_area_m2"]

    n_windows, n_rows, n_cols = accel_map.acceleration_zscore.shape

    logger.info(
        "Running Tier 0 detection: σ=%.1f, min_pixels=%d, min_area=%d m²",
        sigma, min_pixels, min_area,
    )

    # Estimate pixel area from coordinate grid
    pixel_area = _estimate_pixel_area(latitude, longitude)

    all_flags: list[AnomalyFlag] = []
    flag_id = 0

    # Process each time window, focusing on the most recent ones
    # (pre-failure acceleration is most diagnostic near the end)
    for w in range(n_windows):
        zscore_map = accel_map.acceleration_zscore[w]
        accel_vals = accel_map.acceleration[w]

        # Threshold: pixels exceeding sigma threshold
        # Use absolute value — both positive and negative acceleration
        # can indicate instability depending on viewing geometry
        anomalous = np.abs(zscore_map) > sigma

        # Skip windows with no anomalies
        if not np.any(anomalous):
            continue

        # Spatial clustering
        clusters = _cluster_anomalous_pixels(
            anomalous,
            latitude,
            longitude,
            max_distance_m=clust["max_distance_m"],
            min_samples=min_pixels,
        )

        for cluster_label in np.unique(clusters):
            if cluster_label == -1:
                continue  # noise label from DBSCAN

            mask = clusters == cluster_label
            n_pix = np.count_nonzero(mask)
            area = n_pix * pixel_area

            if n_pix < min_pixels or area < min_area:
                continue

            # Extract cluster properties
            rows, cols = np.where(mask)
            zscores = zscore_map[mask]
            accels = accel_vals[mask]

            peak_idx = np.argmax(np.abs(zscores))
            peak_row, peak_col = rows[peak_idx], cols[peak_idx]

            # Composite score: combines magnitude, spatial extent, and persistence
            mean_z = np.nanmean(np.abs(zscores))
            peak_z = np.nanmax(np.abs(zscores))
            spatial_weight = np.log10(max(1, area))
            score = peak_z * 0.5 + mean_z * 0.3 + spatial_weight * 0.2

            flag = AnomalyFlag(
                flag_id=flag_id,
                score=score,
                peak_zscore=float(peak_z),
                mean_zscore=float(mean_z),
                n_pixels=n_pix,
                area_m2=float(area),
                center_lat=float(np.mean(latitude[mask])),
                center_lon=float(np.mean(longitude[mask])),
                peak_lat=float(latitude[peak_row, peak_col]),
                peak_lon=float(longitude[peak_row, peak_col]),
                acceleration_m_yr2=float(accels[peak_idx]),
                pixel_indices=np.column_stack([rows, cols]),
                window_index=w,
                window_date=float(accel_map.window_centers[w]),
                detection_details={
                    "tag": "acceleration",
                    "sigma_threshold": sigma,
                    "window_size_days": int(
                        accel_map.window_centers[1] - accel_map.window_centers[0]
                    )
                    if len(accel_map.window_centers) > 1
                    else 0,
                },
            )

            all_flags.append(flag)
            flag_id += 1

    # Step-change detection: catches abrupt, single-epoch jumps that
    # the acceleration detector's trend-based model would miss (the
    # Nepal 2026 failure mode). Merges (union) with the acceleration
    # flags above; overlapping spatial clusters are resolved by
    # _deduplicate_flags, which keeps the higher-scoring flag.
    if dates is not None and displacement is not None:
        step_flags = _detect_step_change_anomalies(
            dates, displacement, latitude, longitude, config, flag_id,
        )
        all_flags.extend(step_flags)
        flag_id += len(step_flags)

    # Deduplicate: if the same spatial cluster appears in multiple
    # time windows (or across detectors), keep only the
    # highest-scoring instance
    flags = _deduplicate_flags(all_flags, latitude, longitude)

    # Voight analysis on top candidates
    if det.get("voight", {}).get("enabled", False):
        flags = _apply_voight_analysis(
            flags, accel_map, det["voight"]
        )

    # Sort by score (highest first)
    flags.sort(key=lambda f: f.score, reverse=True)

    logger.info(
        "Tier 0 complete: %d flags from %d raw detections across %d windows",
        len(flags), len(all_flags), n_windows,
    )

    for f in flags[:10]:
        logger.info(
            "  Flag %d: score=%.2f, z=%.1f, area=%.0f m², "
            "lat=%.4f lon=%.4f",
            f.flag_id, f.score, f.peak_zscore, f.area_m2,
            f.center_lat, f.center_lon,
        )

    return flags


def _coords_are_projected(latitude: np.ndarray, longitude: np.ndarray) -> bool:
    """Detect whether coordinates are in a projected CRS (meters) vs geographic (degrees)."""
    lat_range = np.nanmax(latitude) - np.nanmin(latitude)
    lon_range = np.nanmax(longitude) - np.nanmin(longitude)
    # Geographic coordinates: lat ∈ [-90, 90], lon ∈ [-180, 180]
    # Projected (UTM): values in hundreds of thousands to millions
    return lat_range > 1000 or lon_range > 1000


def _to_meters(
    lats: np.ndarray, lons: np.ndarray, projected: bool
) -> np.ndarray:
    """Convert coordinate arrays to [N, 2] meter positions."""
    if projected:
        # Already in meters (UTM or similar)
        return np.column_stack([lats, lons])
    else:
        lat_m = lats * 111_320
        lon_m = lons * 111_320 * np.cos(np.radians(np.mean(lats)))
        return np.column_stack([lat_m, lon_m])


def _cluster_anomalous_pixels(
    anomalous: np.ndarray,
    latitude: np.ndarray,
    longitude: np.ndarray,
    max_distance_m: float,
    min_samples: int,
) -> np.ndarray:
    """
    Cluster spatially connected anomalous pixels using DBSCAN.

    Returns an array of cluster labels (same shape as anomalous),
    with -1 for non-anomalous or noise pixels.
    """
    rows, cols = np.where(anomalous)
    if len(rows) == 0:
        return np.full(anomalous.shape, -1, dtype=int)

    # Convert pixel coordinates to meters
    lats = latitude[rows, cols]
    lons = longitude[rows, cols]
    projected = _coords_are_projected(latitude, longitude)
    coords_m = _to_meters(lats, lons, projected)

    # DBSCAN clustering
    clustering = DBSCAN(
        eps=max_distance_m,
        min_samples=min_samples,
        metric="euclidean",
    ).fit(coords_m)

    labels = np.full(anomalous.shape, -1, dtype=int)
    labels[rows, cols] = clustering.labels_

    return labels


def _estimate_pixel_area(latitude: np.ndarray, longitude: np.ndarray) -> float:
    """Estimate the area of a single pixel in m² from coordinate grids."""
    if latitude.shape[0] < 2 or latitude.shape[1] < 2:
        return 900.0  # default ~30m × 30m for Sentinel-1

    dlat = np.abs(latitude[1, 0] - latitude[0, 0])
    dlon = np.abs(longitude[0, 1] - longitude[0, 0])

    if _coords_are_projected(latitude, longitude):
        # Coordinates already in meters
        return dlat * dlon
    else:
        mean_lat = np.nanmean(latitude)
        lat_m = dlat * 111_320
        lon_m = dlon * 111_320 * np.cos(np.radians(mean_lat))
        return lat_m * lon_m


def _detect_step_change_anomalies(
    dates: np.ndarray,
    displacement: np.ndarray,
    latitude: np.ndarray,
    longitude: np.ndarray,
    config: dict,
    start_flag_id: int,
) -> list[AnomalyFlag]:
    """
    Tier 0 screening for abrupt, single-epoch step-change displacement.

    Complements the acceleration-based detector in detect_anomalies():
    that detector looks for a *sustained* speed-up (Voight-style
    pre-failure acceleration), while this one flags a sudden
    displacement jump between two consecutive epochs with no gradual
    build-up — the pattern seen in the Nepal 2026 collapse (3.5 m in
    one 12-day cycle), where velocities decelerated after the rupture
    rather than continuing to accelerate.

    Parameters
    ----------
    dates : np.ndarray
        Acquisition dates as ordinal days, shape [n_epochs].
    displacement : np.ndarray
        Raw LOS displacement in meters, shape [n_epochs, n_rows, n_cols].
    latitude, longitude : np.ndarray
        Coordinate grids, shape [n_rows, n_cols].
    config : dict
        Detection configuration (from config YAML 'detect' section).
        Reads 'detect.step_change.sigma_threshold' (default 5.0) and
        'detect.step_change.min_displacement_m' (default 0.5).
    start_flag_id : int
        First flag_id to assign, so ids stay unique alongside flags
        from the acceleration-based detector.

    Returns
    -------
    list[AnomalyFlag]
        Step-change flags, each tagged
        detection_details["tag"] == "step_change".
    """
    det = config["detect"]
    sc_config = det.get("step_change", {})
    sigma = sc_config.get("sigma_threshold", 5.0)
    min_disp = sc_config.get("min_displacement_m", 0.5)
    clust = det["clustering"]
    min_pixels = clust["min_cluster_pixels"]
    min_area = clust["min_area_m2"]

    logger.info(
        "Running step-change detection: σ=%.1f, min_displacement=%.2fm, "
        "min_pixels=%d, min_area=%d m²",
        sigma, min_disp, min_pixels, min_area,
    )

    step_map = detect_step_changes(
        dates,
        displacement,
        sigma_threshold=sigma,
        min_displacement_m=min_disp,
    )

    n_epochs = step_map.step_change.shape[0]
    pixel_area = _estimate_pixel_area(latitude, longitude)

    flags: list[AnomalyFlag] = []
    flag_id = start_flag_id

    for e in range(n_epochs):
        step_mask = step_map.step_change[e]
        if not np.any(step_mask):
            continue

        magnitudes = step_map.magnitude[e]

        clusters = _cluster_anomalous_pixels(
            step_mask,
            latitude,
            longitude,
            max_distance_m=clust["max_distance_m"],
            min_samples=min_pixels,
        )

        for cluster_label in np.unique(clusters):
            if cluster_label == -1:
                continue  # noise label from DBSCAN

            mask = clusters == cluster_label
            n_pix = np.count_nonzero(mask)
            area = n_pix * pixel_area

            if n_pix < min_pixels or area < min_area:
                continue

            rows, cols = np.where(mask)
            mags = magnitudes[mask]

            peak_idx = np.argmax(np.abs(mags))
            peak_row, peak_col = rows[peak_idx], cols[peak_idx]

            mean_mag = float(np.nanmean(np.abs(mags)))
            peak_mag = float(np.nanmax(np.abs(mags)))

            # Express the jump in units of the configured minimum, so
            # the score is comparable in scale to the acceleration
            # detector's z-score-based score.
            unit = min_disp if min_disp > 0 else 1.0
            peak_score_units = peak_mag / unit
            mean_score_units = mean_mag / unit

            spatial_weight = np.log10(max(1, area))
            score = (
                peak_score_units * 0.5
                + mean_score_units * 0.3
                + spatial_weight * 0.2
            )

            flag = AnomalyFlag(
                flag_id=flag_id,
                score=score,
                peak_zscore=peak_score_units,
                mean_zscore=mean_score_units,
                n_pixels=n_pix,
                area_m2=float(area),
                center_lat=float(np.mean(latitude[mask])),
                center_lon=float(np.mean(longitude[mask])),
                peak_lat=float(latitude[peak_row, peak_col]),
                peak_lon=float(longitude[peak_row, peak_col]),
                acceleration_m_yr2=float("nan"),
                pixel_indices=np.column_stack([rows, cols]),
                window_index=e,
                window_date=float(dates[e]),
                detection_details={
                    "tag": "step_change",
                    "sigma_threshold": sigma,
                    "min_displacement_m": min_disp,
                    "displacement_jump_m": peak_mag,
                },
            )

            flags.append(flag)
            flag_id += 1

    return flags


def _deduplicate_flags(
    flags: list[AnomalyFlag],
    latitude: np.ndarray,
    longitude: np.ndarray,
    merge_distance_m: float = 500.0,
) -> list[AnomalyFlag]:
    """
    Merge flags from different time windows that overlap spatially.

    Keeps the highest-scoring flag from each spatial cluster.
    """
    if not flags:
        return []

    # Group by spatial proximity of cluster centers
    centers = np.array([[f.center_lat, f.center_lon] for f in flags])
    projected = np.max(centers[:, 0]) - np.min(centers[:, 0]) > 1000
    if projected:
        centers_m = centers.copy()
    else:
        mean_lat = np.mean(centers[:, 0])
        centers_m = centers.copy()
        centers_m[:, 0] *= 111_320
        centers_m[:, 1] *= 111_320 * np.cos(np.radians(mean_lat))

    if len(centers_m) == 1:
        return flags

    spatial_clustering = DBSCAN(
        eps=merge_distance_m,
        min_samples=1,
        metric="euclidean",
    ).fit(centers_m)

    # Keep highest-scoring flag per spatial cluster
    best = {}
    for flag, label in zip(flags, spatial_clustering.labels_):
        if label not in best or flag.score > best[label].score:
            best[label] = flag

    return list(best.values())


def _apply_voight_analysis(
    flags: list[AnomalyFlag],
    accel_map: AccelerationMap,
    voight_config: dict,
) -> list[AnomalyFlag]:
    """
    Apply Voight's failure law analysis to flagged sites.

    For each flag, extracts the velocity time series at the peak
    anomaly pixel and fits the inverse-velocity linear trend.
    """
    min_points = voight_config.get("min_points", 5)

    for flag in flags:
        # Get velocity time series at peak pixel location
        peak_row = flag.pixel_indices[
            np.argmax(np.abs(
                accel_map.acceleration_zscore[
                    flag.window_index,
                    flag.pixel_indices[:, 0],
                    flag.pixel_indices[:, 1],
                ]
            ))
        ]
        row, col = peak_row

        velocities = accel_map.velocity_residual[:, row, col]
        dates = accel_map.window_centers

        result = fit_voight(dates, np.abs(velocities), min_points=min_points)
        if result is not None:
            r2_threshold = voight_config.get("r_squared_threshold", 0.7)
            if result["r_squared"] >= r2_threshold:
                flag.voight_fit = result
                # Boost score for flags with Voight fit
                flag.score *= 1.5
                logger.info(
                    "  Flag %d: Voight fit R²=%.2f, predicted failure in %.0f days",
                    flag.flag_id,
                    result["r_squared"],
                    result["days_until_failure"],
                )

    return flags
