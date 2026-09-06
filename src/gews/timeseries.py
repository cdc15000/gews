"""
Time-series analysis module — decomposition, trend fitting, and
acceleration estimation for InSAR displacement time series.

Decomposes each pixel's displacement history into:
    - Linear trend (steady-state velocity)
    - Seasonal component (annual + semi-annual harmonics)
    - Residual (detrended, deseasoned signal)
    - Acceleration (time derivative of residual velocity)

The acceleration signal is what matters for collapse forecasting.
A glacier moving at constant velocity is stable. A glacier that
is accelerating is potentially approaching failure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from scipy import optimize

logger = logging.getLogger(__name__)


@dataclass
class DecomposedPixel:
    """Decomposition results for a single pixel's time series."""

    dates: np.ndarray           # ordinal days
    displacement: np.ndarray    # observed displacement (m)
    trend: np.ndarray           # fitted linear trend (m)
    seasonal: np.ndarray        # fitted seasonal component (m)
    residual: np.ndarray        # displacement - trend - seasonal (m)
    velocity: float             # linear velocity (m/yr)
    velocity_std: float         # uncertainty on velocity (m/yr)
    amplitude_annual: float     # annual seasonal amplitude (m)
    amplitude_semi: float       # semi-annual seasonal amplitude (m)
    r_squared: float            # goodness of fit


@dataclass
class StepChangeMap:
    """
    Step-change (abrupt jump) detection results for all pixels.

    Attributes
    ----------
    dates : np.ndarray
        Acquisition dates as ordinal days, shape [n_epochs].
    step_change : np.ndarray
        Boolean flag per pixel per epoch marking an abrupt
        displacement jump, shape [n_epochs, n_rows, n_cols].
        Epoch 0 is always False (no prior epoch to difference against).
    magnitude : np.ndarray
        Signed inter-epoch displacement jump in meters,
        shape [n_epochs, n_rows, n_cols]. NaN at epoch 0.
    """

    dates: np.ndarray
    step_change: np.ndarray
    magnitude: np.ndarray


@dataclass
class AccelerationMap:
    """
    Acceleration estimates for all pixels over sliding windows.

    Attributes
    ----------
    window_centers : np.ndarray
        Center dates of each sliding window (ordinal days), shape [n_windows].
    acceleration : np.ndarray
        Acceleration in m/yr² per pixel per window,
        shape [n_windows, n_rows, n_cols].
    acceleration_zscore : np.ndarray
        Acceleration normalized by each pixel's historical variability,
        shape [n_windows, n_rows, n_cols]. This is the anomaly score.
    velocity_residual : np.ndarray
        Detrended, deseasoned velocity per window,
        shape [n_windows, n_rows, n_cols].
    """

    window_centers: np.ndarray
    acceleration: np.ndarray
    acceleration_zscore: np.ndarray
    velocity_residual: np.ndarray


def decompose_pixel(
    dates: np.ndarray,
    displacement: np.ndarray,
    n_harmonics: int = 2,
) -> DecomposedPixel | None:
    """
    Decompose a single pixel's displacement time series into trend,
    seasonal, and residual components.

    Uses least-squares fit of:
        d(t) = v·t + Σ[A_k·sin(2πk·t/T) + B_k·cos(2πk·t/T)] + offset

    where T = 365.25 days, k = 1..n_harmonics.

    Parameters
    ----------
    dates : np.ndarray
        Acquisition dates as ordinal days.
    displacement : np.ndarray
        LOS displacement in meters.
    n_harmonics : int
        Number of harmonic terms (1 = annual only, 2 = annual + semi-annual).

    Returns
    -------
    DecomposedPixel or None
        None if insufficient valid observations.
    """
    valid = np.isfinite(displacement)
    if valid.sum() < 2 * n_harmonics + 3:
        return None

    t = dates[valid].astype(float)
    d = displacement[valid]

    # Normalize time for numerical stability
    t0 = t[0]
    t_norm = (t - t0) / 365.25  # in years

    # Build design matrix: [1, t, sin(2π·t/T), cos(2π·t/T), ...]
    T_year = 365.25
    t_days = t - t0

    cols = [np.ones_like(t_norm), t_norm]
    for k in range(1, n_harmonics + 1):
        omega = 2 * np.pi * k / T_year
        cols.append(np.sin(omega * t_days))
        cols.append(np.cos(omega * t_days))

    A = np.column_stack(cols)

    # Solve via least squares
    result = np.linalg.lstsq(A, d, rcond=None)
    params = result[0]

    fitted = A @ params
    residual_vals = d - fitted

    # Extract components
    offset = params[0]
    velocity = params[1]  # m/yr

    # Velocity uncertainty from residual variance
    if len(d) > len(params):
        residual_var = np.sum(residual_vals**2) / (len(d) - len(params))
        ATA_inv = np.linalg.pinv(A.T @ A)
        velocity_std = np.sqrt(residual_var * ATA_inv[1, 1])
    else:
        velocity_std = np.inf

    # Seasonal component
    seasonal_params = params[2:]
    seasonal_cols = cols[2:]
    seasonal = sum(p * c for p, c in zip(seasonal_params, seasonal_cols))

    trend = offset + velocity * t_norm

    # Amplitudes
    amp_annual = 0.0
    amp_semi = 0.0
    if n_harmonics >= 1:
        amp_annual = np.sqrt(params[2] ** 2 + params[3] ** 2)
    if n_harmonics >= 2:
        amp_semi = np.sqrt(params[4] ** 2 + params[5] ** 2)

    # R²
    ss_res = np.sum(residual_vals**2)
    ss_tot = np.sum((d - np.mean(d)) ** 2)
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # Expand back to full date array (with NaN for missing dates)
    trend_full = np.full_like(displacement, np.nan)
    seasonal_full = np.full_like(displacement, np.nan)
    residual_full = np.full_like(displacement, np.nan)

    trend_full[valid] = trend
    seasonal_full[valid] = seasonal
    residual_full[valid] = residual_vals

    return DecomposedPixel(
        dates=dates,
        displacement=displacement,
        trend=trend_full,
        seasonal=seasonal_full,
        residual=residual_full,
        velocity=velocity,
        velocity_std=velocity_std,
        amplitude_annual=amp_annual,
        amplitude_semi=amp_semi,
        r_squared=r_squared,
    )


def compute_acceleration_map(
    dates: np.ndarray,
    displacement: np.ndarray,
    window_size_days: int = 60,
    step_days: int = 12,
    n_harmonics: int = 2,
) -> AccelerationMap:
    """
    Compute sliding-window acceleration for all pixels.

    For each window:
    1. Fit a linear velocity to the detrended, deseasoned residuals
    2. Compute acceleration as the change in velocity between windows
    3. Normalize by each pixel's historical variability (z-score)

    Parameters
    ----------
    dates : np.ndarray
        Ordinal days, shape [n_dates].
    displacement : np.ndarray
        Displacement in meters, shape [n_dates, n_rows, n_cols].
    window_size_days : int
        Width of sliding window in days.
    step_days : int
        Step between window centers in days.
    n_harmonics : int
        Harmonics for seasonal model.

    Returns
    -------
    AccelerationMap
    """
    n_dates, n_rows, n_cols = displacement.shape
    dates_float = dates.astype(float)

    logger.info(
        "Computing acceleration map: %d dates, %d×%d pixels, "
        "window=%dd, step=%dd",
        n_dates, n_rows, n_cols, window_size_days, step_days,
    )

    # Step 1: Decompose all pixels to get residuals
    # Reshape to [n_dates, n_pixels] for batch processing
    n_pixels = n_rows * n_cols
    disp_2d = displacement.reshape(n_dates, n_pixels)

    residuals = _batch_decompose_residuals(dates_float, disp_2d, n_harmonics)
    # residuals shape: [n_dates, n_pixels]

    # Step 2: Sliding window velocity estimation on residuals
    window_centers = np.arange(
        dates_float[0] + window_size_days / 2,
        dates_float[-1] - window_size_days / 2 + 1,
        step_days,
    )

    n_windows = len(window_centers)
    velocities = np.full((n_windows, n_pixels), np.nan)

    # Detect temporal gaps: if data within a window has an internal gap
    # larger than max_gap_days, the velocity fit is unreliable
    max_gap_days = window_size_days * 0.6  # gap > 60% of window → skip

    for i, center in enumerate(window_centers):
        t_start = center - window_size_days / 2
        t_end = center + window_size_days / 2
        mask = (dates_float >= t_start) & (dates_float <= t_end)

        if mask.sum() < 3:
            continue

        t_win = dates_float[mask]
        r_win = residuals[mask]

        # Check for internal gaps — if data is clustered on one side
        # of the window, the velocity estimate is poorly constrained
        gaps = np.diff(t_win)
        if len(gaps) > 0 and np.max(gaps) > max_gap_days:
            continue

        # Also require data to span at least 40% of the window
        data_span = t_win[-1] - t_win[0]
        if data_span < window_size_days * 0.4:
            continue

        # Fit linear velocity per pixel within window
        t_centered = t_win - t_win.mean()
        t_var = np.sum(t_centered**2)

        if t_var == 0:
            continue

        # Vectorized linear regression across all pixels
        # velocity = Σ(t·r) / Σ(t²) in m/day, convert to m/yr
        velocities[i] = (
            np.nansum(t_centered[:, None] * r_win, axis=0) / t_var * 365.25
        )

    # Step 3: Acceleration = d(velocity)/dt between successive windows
    # Only compute between windows that both have valid velocities
    acceleration = np.full((n_windows, n_pixels), np.nan)
    for i in range(1, n_windows):
        if np.all(np.isnan(velocities[i])) or np.all(np.isnan(velocities[i - 1])):
            continue
        dt_yr = (window_centers[i] - window_centers[i - 1]) / 365.25
        if dt_yr > 0:
            acceleration[i] = (velocities[i] - velocities[i - 1]) / dt_yr

    # Step 4: Z-score normalization against each pixel's own history
    # Use the first 70% of windows as the baseline period, but fall
    # back to the full series when the baseline has too few samples
    # (common with sparse SAR revisits or temporal gaps).
    n_baseline = max(3, int(0.7 * n_windows))
    baseline_accel = acceleration[:n_baseline]

    with np.errstate(invalid="ignore"):
        n_valid_baseline = np.sum(np.isfinite(baseline_accel), axis=0)

        # Fall back to full series when early baseline is too sparse
        use_full = n_valid_baseline < 3
        pixel_mean = np.nanmean(baseline_accel, axis=0)
        pixel_std = np.nanstd(baseline_accel, axis=0)

        if np.any(use_full):
            full_mean = np.nanmean(acceleration, axis=0)
            full_std = np.nanstd(acceleration, axis=0)
            n_valid_full = np.sum(np.isfinite(acceleration), axis=0)
            pixel_mean[use_full] = full_mean[use_full]
            pixel_std[use_full] = full_std[use_full]
            n_valid_baseline[use_full] = n_valid_full[use_full]

        pixel_std[pixel_std < 1e-10] = np.nan  # avoid division by zero
        pixel_mean[n_valid_baseline < 3] = np.nan
        pixel_std[n_valid_baseline < 3] = np.nan

        zscore = (acceleration - pixel_mean[None, :]) / pixel_std[None, :]

    # Reshape back to spatial dimensions
    acceleration_3d = acceleration.reshape(n_windows, n_rows, n_cols)
    zscore_3d = zscore.reshape(n_windows, n_rows, n_cols)
    velocity_3d = velocities.reshape(n_windows, n_rows, n_cols)

    logger.info(
        "Acceleration map complete: %d windows, "
        "max |z-score| = %.1f",
        n_windows,
        np.nanmax(np.abs(zscore_3d)),
    )

    return AccelerationMap(
        window_centers=window_centers,
        acceleration=acceleration_3d,
        acceleration_zscore=zscore_3d,
        velocity_residual=velocity_3d,
    )


def _batch_decompose_residuals(
    dates: np.ndarray,
    displacement_2d: np.ndarray,
    n_harmonics: int,
) -> np.ndarray:
    """
    Vectorized seasonal + trend removal across all pixels at once.

    Rather than looping per-pixel, solves the same design matrix
    against all pixel columns simultaneously.

    Parameters
    ----------
    dates : np.ndarray
        Ordinal days, shape [n_dates].
    displacement_2d : np.ndarray
        Shape [n_dates, n_pixels].
    n_harmonics : int

    Returns
    -------
    np.ndarray
        Residuals, shape [n_dates, n_pixels].
    """
    n_dates, n_pixels = displacement_2d.shape
    t0 = dates[0]
    t_norm = (dates - t0) / 365.25
    t_days = dates - t0

    T_year = 365.25
    cols = [np.ones(n_dates), t_norm]
    for k in range(1, n_harmonics + 1):
        omega = 2 * np.pi * k / T_year
        cols.append(np.sin(omega * t_days))
        cols.append(np.cos(omega * t_days))

    A = np.column_stack(cols)  # [n_dates, n_params]

    # Solve for all pixels at once: params = (AᵀA)⁻¹Aᵀ · displacement
    # Handle NaN by solving per-pixel where needed
    all_valid = np.all(np.isfinite(displacement_2d), axis=0)
    residuals = np.full_like(displacement_2d, np.nan)

    # Fast path: pixels with no NaN
    valid_cols = np.where(all_valid)[0]
    if len(valid_cols) > 0:
        params, _, _, _ = np.linalg.lstsq(A, displacement_2d[:, valid_cols], rcond=None)
        fitted = A @ params
        residuals[:, valid_cols] = displacement_2d[:, valid_cols] - fitted

    # Slow path: pixels with some NaN
    nan_cols = np.where(~all_valid)[0]
    for col in nan_cols:
        d = displacement_2d[:, col]
        good = np.isfinite(d)
        if good.sum() < A.shape[1] + 1:
            continue
        params_col, _, _, _ = np.linalg.lstsq(A[good], d[good], rcond=None)
        residuals[good, col] = d[good] - A[good] @ params_col

    return residuals


def detect_step_changes(
    dates: np.ndarray,
    displacement: np.ndarray,
    sigma_threshold: float = 5.0,
    min_displacement_m: float = 0.5,
    baseline_fraction: float = 0.3,
) -> StepChangeMap:
    """
    Detect abrupt, single-epoch step-change displacement events.

    The acceleration-based detector (compute_acceleration_map) looks
    for a *sustained* increase in velocity — the classic Voight-style
    pre-failure signature. That pattern assumes displacement builds
    up gradually as failure approaches.

    The Nepal 2026 collapse did not follow that pattern: it produced
    3.5 m of displacement in a single 12-day SAR revisit cycle, and
    velocities *decelerated* after the initial rupture rather than
    continuing to accelerate. Voight's inverse-velocity law — which
    depends on velocity climbing smoothly toward infinity — fails on
    this kind of event because there is no smooth climb to fit: the
    slope is a discontinuity, not a trend.

    This detector instead looks directly at the size of the jump
    between consecutive epochs, independent of any trend model:

    1. Compute inter-epoch displacement differences Δd[i] = d[i] - d[i-1]
       for every pixel.
    2. Estimate each pixel's baseline Δd statistics (mean, std) from
       an early, presumed-stable portion of the time series.
    3. Flag epoch i as a step change when the deviation from that
       baseline exceeds `sigma_threshold` baseline standard deviations
       AND the absolute jump exceeds `min_displacement_m` (a floor
       that keeps near-zero-noise pixels, whose baseline std is tiny,
       from tripping the sigma test on ordinary noise).

    Parameters
    ----------
    dates : np.ndarray
        Acquisition dates as ordinal days, shape [n_epochs].
    displacement : np.ndarray
        LOS displacement in meters, shape [n_epochs, n_rows, n_cols].
    sigma_threshold : float
        Number of baseline standard deviations an inter-epoch jump
        must exceed to be flagged.
    min_displacement_m : float
        Absolute minimum jump size (meters) required to flag,
        regardless of sigma. Guards against flagging trivial jumps
        at pixels with near-zero baseline variability.
    baseline_fraction : float
        Fraction of the early time series (by epoch count) used to
        estimate each pixel's baseline Δd mean/std, on the assumption
        that a site starts in a stable state.

    Returns
    -------
    StepChangeMap
    """
    n_epochs, n_rows, n_cols = displacement.shape
    n_pixels = n_rows * n_cols
    disp_2d = displacement.reshape(n_epochs, n_pixels)

    logger.info(
        "Detecting step changes: %d epochs, %d×%d pixels, "
        "sigma=%.1f, min_displacement=%.2fm",
        n_epochs, n_rows, n_cols, sigma_threshold, min_displacement_m,
    )

    # Inter-epoch differences. delta[0] is undefined — no prior epoch.
    delta = np.full((n_epochs, n_pixels), np.nan)
    delta[1:] = np.diff(disp_2d, axis=0)

    # Baseline period: an early, presumed-stable window of epochs used
    # to characterize each pixel's normal inter-epoch variability.
    n_baseline = max(3, int(baseline_fraction * n_epochs))
    baseline_delta = delta[1 : n_baseline + 1]

    with np.errstate(invalid="ignore"):
        n_valid_baseline = np.sum(np.isfinite(baseline_delta), axis=0)
        baseline_mean = np.nanmean(baseline_delta, axis=0)
        baseline_std = np.nanstd(baseline_delta, axis=0)

        # Pixels with too little baseline data or ~zero variability
        # can't support a meaningful sigma test.
        baseline_std[baseline_std < 1e-10] = np.nan
        baseline_mean[n_valid_baseline < 3] = np.nan
        baseline_std[n_valid_baseline < 3] = np.nan

        deviation = np.abs(delta - baseline_mean[None, :])
        exceeds_sigma = deviation > (sigma_threshold * baseline_std[None, :])

    exceeds_min = np.abs(delta) > min_displacement_m

    # NaN comparisons evaluate to False, so pixels lacking a valid
    # baseline or a valid delta are never flagged.
    step_flag = exceeds_sigma & exceeds_min

    step_flag_3d = step_flag.reshape(n_epochs, n_rows, n_cols)
    magnitude_3d = delta.reshape(n_epochs, n_rows, n_cols)

    logger.info(
        "Step-change detection complete: %d flagged (epoch, pixel) events",
        int(np.sum(step_flag_3d)),
    )

    return StepChangeMap(
        dates=dates,
        step_change=step_flag_3d,
        magnitude=magnitude_3d,
    )


def fit_voight(
    dates: np.ndarray,
    velocity: np.ndarray,
    min_points: int = 5,
) -> dict | None:
    """
    Fit Voight's empirical failure law to a velocity time series.

    Voight's relation predicts that the inverse of velocity (1/v)
    decreases linearly toward zero as failure approaches. The
    x-intercept of this linear fit gives the predicted failure time.

    This is the same relation used in volcanic eruption forecasting
    (Voight 1988) and has been applied to landslide prediction.

    Parameters
    ----------
    dates : np.ndarray
        Ordinal days of velocity estimates.
    velocity : np.ndarray
        Velocity values (same units, e.g., m/yr). Must be positive
        (accelerating toward failure).
    min_points : int
        Minimum number of points for fit.

    Returns
    -------
    dict or None
        {'predicted_failure_date': ordinal, 'r_squared': float,
         'inverse_velocity_slope': float}
        None if fit fails or insufficient data.
    """
    valid = np.isfinite(velocity) & (velocity > 0)
    if valid.sum() < min_points:
        return None

    t = dates[valid].astype(float)
    v = velocity[valid]
    inv_v = 1.0 / v

    # Check for decreasing inverse velocity (accelerating)
    if inv_v[-1] >= inv_v[0]:
        return None  # not accelerating

    # Linear fit to inverse velocity
    coeffs = np.polyfit(t, inv_v, 1)
    slope, intercept = coeffs

    if slope >= 0:
        return None  # inverse velocity not decreasing

    # Predicted failure: when 1/v = 0, i.e., t = -intercept/slope
    t_failure = -intercept / slope

    # R²
    inv_v_pred = np.polyval(coeffs, t)
    ss_res = np.sum((inv_v - inv_v_pred) ** 2)
    ss_tot = np.sum((inv_v - np.mean(inv_v)) ** 2)
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # Only return if fit is reasonable and failure is in the future
    # relative to the last observation
    if r_squared < 0.5 or t_failure <= t[-1]:
        return None

    return {
        "predicted_failure_date": t_failure,
        "r_squared": r_squared,
        "inverse_velocity_slope": slope,
        "days_until_failure": t_failure - t[-1],
    }
