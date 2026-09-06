"""Tests for the time-series analysis module."""

import numpy as np
import pytest

from gews.timeseries import (
    AccelerationMap,
    DecomposedPixel,
    compute_acceleration_map,
    decompose_pixel,
    fit_voight,
)


class TestDecomposePixel:
    """Tests for single-pixel time-series decomposition."""

    def _make_dates(self, n=50, start=738886, step=12):
        """Generate evenly spaced ordinal dates."""
        return np.arange(start, start + n * step, step)

    def test_linear_trend_recovery(self):
        """Should recover a simple linear trend."""
        dates = self._make_dates(50)
        t_yr = (dates - dates[0]) / 365.25
        velocity = 0.05  # 50 mm/yr
        displacement = velocity * t_yr

        result = decompose_pixel(dates, displacement, n_harmonics=0)

        assert result is not None
        assert abs(result.velocity - velocity) < 0.001
        assert result.r_squared > 0.99

    def test_seasonal_recovery(self):
        """Should recover annual and semi-annual seasonal signals."""
        dates = self._make_dates(80)
        t_days = (dates - dates[0]).astype(float)
        t_yr = t_days / 365.25

        velocity = 0.03
        amp_annual = 0.01
        amp_semi = 0.005

        omega1 = 2 * np.pi / 365.25
        displacement = (
            velocity * t_yr
            + amp_annual * np.sin(omega1 * t_days)
            + amp_semi * np.sin(2 * omega1 * t_days)
        )

        result = decompose_pixel(dates, displacement, n_harmonics=2)

        assert result is not None
        assert abs(result.velocity - velocity) < 0.005
        assert abs(result.amplitude_annual - amp_annual) < 0.003
        assert result.r_squared > 0.95

    def test_handles_nan(self):
        """Should handle missing observations (NaN)."""
        dates = self._make_dates(50)
        t_yr = (dates - dates[0]) / 365.25
        displacement = 0.05 * t_yr

        # Remove some observations
        displacement[10] = np.nan
        displacement[20] = np.nan
        displacement[30] = np.nan

        result = decompose_pixel(dates, displacement, n_harmonics=1)

        assert result is not None
        assert abs(result.velocity - 0.05) < 0.01

    def test_insufficient_data_returns_none(self):
        """Should return None if too few observations."""
        dates = np.array([1, 2, 3])
        displacement = np.array([0.0, 0.001, 0.002])

        result = decompose_pixel(dates, displacement, n_harmonics=2)
        assert result is None

    def test_all_nan_returns_none(self):
        """Should return None if all data is NaN."""
        dates = self._make_dates(20)
        displacement = np.full(20, np.nan)

        result = decompose_pixel(dates, displacement, n_harmonics=1)
        assert result is None


class TestAccelerationMap:
    """Tests for the acceleration map computation."""

    def _make_cube(self, n_dates=50, n_rows=20, n_cols=25):
        """Generate a synthetic displacement cube."""
        dates = np.arange(738886, 738886 + n_dates * 12, 12)
        t_yr = (dates - dates[0]).astype(float) / 365.25

        # Uniform velocity, no acceleration
        velocity = 0.04
        displacement = np.zeros((n_dates, n_rows, n_cols))
        for i in range(n_dates):
            displacement[i] = velocity * t_yr[i]

        return dates, displacement

    def test_no_acceleration_low_zscore(self):
        """Constant-velocity field should produce low z-scores."""
        dates, disp = self._make_cube()

        # Add small noise so decomposition doesn't produce zeros
        rng = np.random.default_rng(42)
        disp += rng.normal(0, 0.001, disp.shape)

        accel = compute_acceleration_map(
            dates, disp, window_size_days=60, step_days=12
        )

        assert isinstance(accel, AccelerationMap)
        assert accel.acceleration_zscore.shape[0] > 0

        # Most z-scores should be small (< 2) for steady motion
        valid = np.isfinite(accel.acceleration_zscore)
        if valid.any():
            assert np.nanpercentile(np.abs(accel.acceleration_zscore[valid]), 95) < 3.0

    def test_injected_acceleration_detected(self):
        """An injected acceleration signal should produce high z-scores."""
        dates, disp = self._make_cube(n_dates=60)
        t_yr = (dates - dates[0]).astype(float) / 365.25

        # Inject strong acceleration in a small patch for the last 20%
        onset_idx = int(0.8 * len(dates))
        for i in range(onset_idx, len(dates)):
            t_since = t_yr[i] - t_yr[onset_idx]
            # Quadratic acceleration
            disp[i, 8:12, 10:14] -= 0.5 * 0.2 * t_since**2

        rng = np.random.default_rng(42)
        disp += rng.normal(0, 0.001, disp.shape)

        accel = compute_acceleration_map(
            dates, disp, window_size_days=60, step_days=12
        )

        # The accelerating patch should have high z-scores
        # in later windows
        last_window_z = accel.acceleration_zscore[-1]
        patch_z = last_window_z[8:12, 10:14]

        valid_patch = np.isfinite(patch_z)
        if valid_patch.any():
            max_z = np.nanmax(np.abs(patch_z))
            assert max_z > 2.0, f"Expected high z-score in accelerating patch, got {max_z}"

    def test_output_shapes(self):
        """Output arrays should have consistent shapes."""
        dates, disp = self._make_cube(n_dates=40)

        rng = np.random.default_rng(42)
        disp += rng.normal(0, 0.001, disp.shape)

        accel = compute_acceleration_map(
            dates, disp, window_size_days=60, step_days=12
        )

        n_windows = len(accel.window_centers)
        assert accel.acceleration.shape == (n_windows, 20, 25)
        assert accel.acceleration_zscore.shape == (n_windows, 20, 25)
        assert accel.velocity_residual.shape == (n_windows, 20, 25)


class TestVoight:
    """Tests for Voight's failure law fitting."""

    def test_accelerating_velocity(self):
        """Should fit inverse-velocity trend for accelerating motion."""
        dates = np.arange(0, 100, 5, dtype=float)

        # Velocity increasing toward infinity (approaching failure)
        # v(t) = v0 / (1 - t/t_fail), so 1/v decreases linearly
        t_fail = 120.0
        v0 = 1.0
        velocity = v0 / (1 - dates / t_fail)

        result = fit_voight(dates, velocity)

        assert result is not None
        assert result["r_squared"] > 0.95
        assert abs(result["predicted_failure_date"] - t_fail) < 5

    def test_constant_velocity_returns_none(self):
        """Should return None for non-accelerating velocity."""
        dates = np.arange(0, 100, 5, dtype=float)
        velocity = np.full_like(dates, 2.0)

        result = fit_voight(dates, velocity)
        assert result is None

    def test_decelerating_returns_none(self):
        """Should return None for decelerating motion."""
        dates = np.arange(0, 100, 5, dtype=float)
        velocity = 10.0 / (1 + dates / 50)  # decreasing

        result = fit_voight(dates, velocity)
        assert result is None

    def test_insufficient_data_returns_none(self):
        """Should return None with too few points."""
        dates = np.array([0.0, 5.0, 10.0])
        velocity = np.array([1.0, 2.0, 4.0])

        result = fit_voight(dates, velocity, min_points=5)
        assert result is None
