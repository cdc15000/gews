"""
Cascade risk assessment module — Tier 1 filtering.

Evaluates whether a flagged unstable site can produce a dangerous
downstream cascade (debris flow, river blockage, flood). Flags that
fail these physical/geometric tests are deprioritized regardless
of their deformation anomaly score.

Filters:
    1. Volume estimation — is the potentially unstable mass large enough?
    2. Geometric plausibility — is it above a confined valley or river?
    3. Downstream exposure — are people or infrastructure at risk?
    4. Runout analysis — can the debris reach a river or settlement?

All filters use freely available data (DEMs, population layers)
and require no labeled collapse examples.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import numpy as np

from gews.detect import AnomalyFlag

logger = logging.getLogger(__name__)


class RiskLevel(Enum):
    """Cascade risk classification for Tier 1 output."""

    HIGH = "high"          # passes all filters, significant downstream exposure
    MODERATE = "moderate"  # passes geometric filters, limited exposure
    LOW = "low"            # fails volume or geometry filters
    MINIMAL = "minimal"    # isolated, no downstream pathway


@dataclass
class CascadeAssessment:
    """
    Tier 1 cascade risk assessment for a single flagged site.
    """

    flag: AnomalyFlag
    risk_level: RiskLevel

    # Volume estimate
    estimated_volume_m3: float
    volume_sufficient: bool  # exceeds minimum threshold

    # Geometric analysis
    slope_angle_deg: float
    elevation_m: float
    above_valley: bool      # deforming mass is above a confined valley
    valley_width_m: float | None  # width of valley below, if applicable

    # Runout analysis
    runout_distance_m: float
    runout_reaches_river: bool
    angle_of_reach_deg: float

    # Downstream exposure
    population_exposed: int
    settlements_exposed: list[str] = field(default_factory=list)
    infrastructure_at_risk: list[str] = field(default_factory=list)

    # River damming potential
    dam_potential: bool = False
    estimated_lake_volume_m3: float = 0.0

    # Combined score for ranking
    cascade_score: float = 0.0

    @property
    def passes_tier1(self) -> bool:
        """Whether this site should advance to Tier 2."""
        return self.risk_level in (RiskLevel.HIGH, RiskLevel.MODERATE)


def assess_cascade_risk(
    flags: list[AnomalyFlag],
    dem: np.ndarray,
    dem_lat: np.ndarray,
    dem_lon: np.ndarray,
    config: dict,
    population_grid: np.ndarray | None = None,
) -> list[CascadeAssessment]:
    """
    Run Tier 1 cascade risk assessment on flagged sites.

    Parameters
    ----------
    flags : list[AnomalyFlag]
        Tier 0 detection output.
    dem : np.ndarray
        Digital elevation model (meters), shape [n_rows, n_cols].
    dem_lat, dem_lon : np.ndarray
        Coordinate grids for the DEM.
    config : dict
        Cascade configuration (from YAML 'cascade' section).
    population_grid : np.ndarray or None
        Population density grid (people/km²), aligned to DEM.
        If None, exposure check is skipped.

    Returns
    -------
    list[CascadeAssessment]
        Assessments sorted by cascade_score (highest risk first).
    """
    cascade_cfg = config["cascade"]
    min_volume = cascade_cfg["min_volume_m3"]
    max_runout = cascade_cfg["exposure"]["max_runout_km"] * 1000
    aor_threshold = cascade_cfg["exposure"]["angle_of_reach_deg"]
    valley_width_thresh = cascade_cfg.get("valley_width_threshold_m", 500)

    assessments = []

    for flag in flags:
        logger.info("Assessing cascade risk for flag %d (%.4f°N, %.4f°E)",
                     flag.flag_id, flag.center_lat, flag.center_lon)

        # Find nearest DEM pixel to flag center
        row_idx, col_idx = _nearest_pixel(
            dem_lat, dem_lon, flag.center_lat, flag.center_lon
        )

        # 1. Volume estimation
        volume = _estimate_failure_volume(
            flag, dem, dem_lat, dem_lon, row_idx, col_idx
        )

        # 2. Slope and elevation
        slope = _compute_slope_angle(dem, dem_lat, dem_lon, row_idx, col_idx)
        elevation = float(dem[row_idx, col_idx])

        # 3. Valley analysis
        above_valley, valley_width = _analyze_valley_below(
            dem, dem_lat, dem_lon, row_idx, col_idx
        )

        # 4. Runout analysis (empirical angle-of-reach)
        height_drop = _max_height_drop(dem, row_idx, col_idx, max_runout, dem_lat, dem_lon)
        aor = np.degrees(np.arctan2(height_drop, max_runout)) if max_runout > 0 else 90
        runout_distance = (
            height_drop / np.tan(np.radians(aor_threshold))
            if aor_threshold > 0
            else 0
        )
        reaches_river = above_valley and runout_distance > 0

        # 5. Population exposure
        pop = 0
        if population_grid is not None:
            pop = _estimate_exposed_population(
                population_grid, dem_lat, dem_lon,
                flag.center_lat, flag.center_lon,
                runout_distance,
            )

        # 6. Dam potential
        dam_pot = (
            above_valley
            and volume > min_volume
            and valley_width is not None
            and valley_width < valley_width_thresh
        )

        # Risk classification
        volume_ok = volume >= min_volume
        if volume_ok and above_valley and (pop > 0 or dam_pot):
            risk = RiskLevel.HIGH
        elif volume_ok and (above_valley or pop > 0):
            risk = RiskLevel.MODERATE
        elif volume_ok:
            risk = RiskLevel.LOW
        else:
            risk = RiskLevel.MINIMAL

        # Composite cascade score
        cascade_score = _compute_cascade_score(
            flag.score, volume, pop, dam_pot, above_valley, slope
        )

        assessment = CascadeAssessment(
            flag=flag,
            risk_level=risk,
            estimated_volume_m3=volume,
            volume_sufficient=volume_ok,
            slope_angle_deg=slope,
            elevation_m=elevation,
            above_valley=above_valley,
            valley_width_m=valley_width,
            runout_distance_m=runout_distance,
            runout_reaches_river=reaches_river,
            angle_of_reach_deg=aor,
            population_exposed=pop,
            dam_potential=dam_pot,
            cascade_score=cascade_score,
        )

        assessments.append(assessment)

        logger.info(
            "  Flag %d: risk=%s, volume=%.0f m³, slope=%.1f°, "
            "elev=%.0f m, valley=%s, pop=%d, dam=%s",
            flag.flag_id, risk.value, volume, slope,
            elevation, above_valley, pop, dam_pot,
        )

    assessments.sort(key=lambda a: a.cascade_score, reverse=True)

    n_pass = sum(1 for a in assessments if a.passes_tier1)
    logger.info(
        "Tier 1 complete: %d of %d flags pass (≥ MODERATE risk)",
        n_pass, len(assessments),
    )

    return assessments


def _nearest_pixel(
    lat_grid: np.ndarray, lon_grid: np.ndarray,
    target_lat: float, target_lon: float,
) -> tuple[int, int]:
    """Find the row, col index of the nearest pixel to a target coordinate."""
    dist = (lat_grid - target_lat) ** 2 + (lon_grid - target_lon) ** 2
    return np.unravel_index(np.nanargmin(dist), dist.shape)


def _estimate_failure_volume(
    flag: AnomalyFlag,
    dem: np.ndarray,
    lat: np.ndarray,
    lon: np.ndarray,
    center_row: int,
    center_col: int,
) -> float:
    """
    Estimate the volume of potentially unstable mass.

    Uses the area of the deformation anomaly and an assumed failure
    depth proportional to the width of the deforming zone.

    This is a rough estimate — adequate for Tier 1 screening but
    not for engineering design.
    """
    area = flag.area_m2

    # Empirical: failure depth ≈ 0.1 × sqrt(area) for rock slopes
    # (Hungr et al., 2014). Conservative for screening.
    width = np.sqrt(area)
    depth = 0.1 * width  # meters

    # Adjust for slope angle
    if center_row < dem.shape[0] and center_col < dem.shape[1]:
        slope_rad = np.radians(
            _compute_slope_angle(dem, lat, lon, center_row, center_col)
        )
        # On steeper slopes, the failure slab is thinner
        depth *= np.cos(slope_rad)

    volume = area * depth
    return max(volume, 0)


def _compute_slope_angle(
    dem: np.ndarray,
    lat: np.ndarray,
    lon: np.ndarray,
    row: int,
    col: int,
    window: int = 3,
) -> float:
    """Compute local slope angle in degrees from DEM."""
    half = window // 2
    r0 = max(0, row - half)
    r1 = min(dem.shape[0], row + half + 1)
    c0 = max(0, col - half)
    c1 = min(dem.shape[1], col + half + 1)

    patch = dem[r0:r1, c0:c1]
    if patch.size < 4:
        return 0.0

    # Pixel spacing in meters
    mean_lat = np.nanmean(lat[r0:r1, c0:c1])
    dy = 111_320 * np.abs(lat[r0, c0] - lat[min(r1 - 1, r0 + 1), c0])
    dx = 111_320 * np.cos(np.radians(mean_lat)) * np.abs(
        lon[r0, c0] - lon[r0, min(c1 - 1, c0 + 1)]
    ) if lon.ndim == 2 else 30.0  # fallback

    if dy == 0:
        dy = 30.0
    if dx == 0:
        dx = 30.0

    # Gradient
    gy, gx = np.gradient(patch, dy, dx)
    slope_rad = np.arctan(np.sqrt(np.nanmean(gx**2) + np.nanmean(gy**2)))
    return float(np.degrees(slope_rad))


def _analyze_valley_below(
    dem: np.ndarray,
    lat: np.ndarray,
    lon: np.ndarray,
    row: int,
    col: int,
    search_radius_pixels: int = 50,
) -> tuple[bool, float | None]:
    """
    Determine if the flagged site is above a confined valley.

    Searches downslope from the site for a local minimum in elevation
    flanked by rising terrain on both sides (valley cross-section).

    Returns (above_valley, valley_width_m).
    """
    n_rows, n_cols = dem.shape

    # Sample elevation profile in steepest descent direction
    r0 = max(0, row - search_radius_pixels)
    r1 = min(n_rows, row + search_radius_pixels + 1)
    c0 = max(0, col - search_radius_pixels)
    c1 = min(n_cols, col + search_radius_pixels + 1)

    patch = dem[r0:r1, c0:c1]
    center_elev = dem[row, col]

    # Find lowest point in search area
    if np.all(np.isnan(patch)):
        return False, None

    min_idx = np.unravel_index(np.nanargmin(patch), patch.shape)
    min_elev = patch[min_idx]

    # Must be significantly below the flagged site
    if center_elev - min_elev < 100:  # less than 100m drop
        return False, None

    # Check if the minimum is in a valley (terrain rises on both sides)
    # Take a cross-section through the minimum perpendicular to the
    # slope direction
    min_row_local, min_col_local = min_idx
    cross_section = patch[min_row_local, :]

    valid = np.isfinite(cross_section)
    if valid.sum() < 5:
        return False, None

    # Valley = minimum with terrain rising on both sides
    cs_min_idx = np.nanargmin(cross_section)
    left_max = np.nanmax(cross_section[:cs_min_idx]) if cs_min_idx > 0 else min_elev
    right_max = np.nanmax(cross_section[cs_min_idx + 1:]) if cs_min_idx < len(cross_section) - 1 else min_elev

    prominence = min(left_max - min_elev, right_max - min_elev)
    if prominence < 50:  # less than 50m prominence = not a clear valley
        return False, None

    # Estimate valley width at the minimum
    above_min = cross_section > (min_elev + prominence * 0.5)
    transitions = np.diff(above_min.astype(int))
    entries = np.where(transitions == -1)[0]
    exits = np.where(transitions == 1)[0]

    if len(entries) > 0 and len(exits) > 0:
        # Width between first descent and first ascent
        mean_lat_val = np.nanmean(lat[r0:r1, c0:c1])
        dlon = np.abs(lon[0, 1] - lon[0, 0]) if lon.ndim == 2 and lon.shape[1] > 1 else 0.00028
        pixel_width_m = dlon * 111_320 * np.cos(np.radians(mean_lat_val))
        valley_width = abs(exits[0] - entries[0]) * pixel_width_m
        return True, float(valley_width)

    return True, None


def _max_height_drop(
    dem: np.ndarray,
    row: int,
    col: int,
    max_distance_m: float,
    lat: np.ndarray,
    lon: np.ndarray,
) -> float:
    """Maximum elevation drop within search radius."""
    # Approximate pixels for max distance
    pixel_size_m = 30.0  # default
    if lat.shape[0] > 1:
        pixel_size_m = np.abs(lat[1, 0] - lat[0, 0]) * 111_320

    radius_px = int(max_distance_m / pixel_size_m) if pixel_size_m > 0 else 100
    radius_px = min(radius_px, max(dem.shape))

    r0 = max(0, row - radius_px)
    r1 = min(dem.shape[0], row + radius_px + 1)
    c0 = max(0, col - radius_px)
    c1 = min(dem.shape[1], col + radius_px + 1)

    patch = dem[r0:r1, c0:c1]
    center_elev = dem[row, col]
    min_elev = np.nanmin(patch)

    return max(0, center_elev - min_elev)


def _estimate_exposed_population(
    pop_grid: np.ndarray,
    lat: np.ndarray,
    lon: np.ndarray,
    center_lat: float,
    center_lon: float,
    runout_m: float,
) -> int:
    """
    Estimate population within the potential runout zone.

    Simple circular buffer for PoC — operational system would use
    actual flow-path modeling.
    """
    if pop_grid is None:
        return 0

    mean_lat = np.nanmean(lat)
    lat_m = (lat - center_lat) * 111_320
    lon_m = (lon - center_lon) * 111_320 * np.cos(np.radians(mean_lat))
    dist = np.sqrt(lat_m**2 + lon_m**2)

    # Only count population downslope (lower elevation)
    in_range = dist <= runout_m
    exposed = pop_grid[in_range]

    return int(np.nansum(exposed))


def _compute_cascade_score(
    anomaly_score: float,
    volume: float,
    population: int,
    dam_potential: bool,
    above_valley: bool,
    slope: float,
) -> float:
    """
    Composite cascade risk score combining deformation anomaly
    with physical cascade potential.
    """
    # Log-scale volume contribution
    vol_score = np.log10(max(1, volume)) / 7  # normalized: 10^7 m³ → 1.0

    # Population contribution (diminishing returns)
    pop_score = np.log10(max(1, population)) / 5  # 100k people → 1.0

    # Binary factors
    dam_score = 0.3 if dam_potential else 0.0
    valley_score = 0.2 if above_valley else 0.0

    # Weighted combination
    score = (
        anomaly_score * 0.3
        + vol_score * 0.25
        + pop_score * 0.2
        + dam_score
        + valley_score
        + (slope / 90) * 0.05  # steeper = riskier
    )

    return score
