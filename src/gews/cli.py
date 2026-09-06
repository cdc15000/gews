"""
GEWS command-line interface.

Usage:
    gews search   --config CONFIG     Search for available Sentinel-1 scenes
    gews download --config CONFIG     Download SLC scenes from ASF
    gews process  --config CONFIG     Run InSAR processing (ISCE-2 + MintPy)
    gews detect   --config CONFIG     Run Tier 0 anomaly detection
    gews assess   --config CONFIG     Run Tier 1 cascade risk assessment
    gews report   --config CONFIG     Generate analysis report
    gews monitor  --config CONFIG     Continuous monitoring for new NISAR data
    gews demo                         Run full pipeline on synthetic data
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click
import yaml

logger = logging.getLogger("gews")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s  %(message)s",
        datefmt="%H:%M:%S",
    )


def _load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        click.echo(f"Configuration file not found: {path}", err=True)
        sys.exit(1)

    with open(path) as f:
        return yaml.safe_load(f)


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Verbose logging")
def main(verbose: bool) -> None:
    """GEWS — Glacier Early Warning System for unstable glacier detection."""
    _setup_logging(verbose)


@main.command()
@click.option("--config", "-c", required=True, help="Path to site config YAML")
def search(config: str) -> None:
    """Search ASF archive for Sentinel-1 scenes covering the study area."""
    from gews.acquire import search_scenes, select_track, summarize_scenes

    cfg = _load_config(config)
    scenes = search_scenes(cfg)

    click.echo(summarize_scenes(scenes))
    click.echo()

    # Show recommended track
    track = select_track(scenes, cfg["acquire"].get("path_number"))
    click.echo(f"\nRecommended: {len(track)} scenes on selected track")


@main.command()
@click.option("--config", "-c", required=True, help="Path to site config YAML")
def download(config: str) -> None:
    """Download SLC scenes from ASF DAAC."""
    from gews.acquire import search_scenes, select_track, download_scenes

    cfg = _load_config(config)
    scenes = search_scenes(cfg)
    track = select_track(scenes, cfg["acquire"].get("path_number"))

    acq = cfg["acquire"]
    download_scenes(
        track,
        output_dir=acq.get("output_dir", "data/slc"),
        n_workers=acq.get("n_workers", 4),
    )


@main.command()
@click.option("--config", "-c", required=True, help="Path to site config YAML")
@click.option("--dry-run", is_flag=True, help="Print commands without executing")
@click.option(
    "--step",
    type=click.Choice(["prepare", "isce", "mintpy", "all"]),
    default="all",
    help="Which processing step to run",
)
def process(config: str, dry_run: bool, step: str) -> None:
    """Run InSAR processing pipeline (ISCE-2 + MintPy)."""
    from gews.process import InSARProcessor

    cfg = _load_config(config)
    proc = InSARProcessor(cfg)

    if step in ("prepare", "all"):
        proc.prepare_stack()

    if step in ("isce", "all"):
        proc.run_isce(dry_run=dry_run)

    if step in ("mintpy", "all"):
        if step == "mintpy":
            proc.generate_mintpy_config()
        proc.run_mintpy(dry_run=dry_run)

    click.echo("Processing complete.")


@main.command()
@click.option("--config", "-c", required=True, help="Path to site config YAML")
@click.option("--output", "-o", default="output", help="Output directory")
def detect(config: str, output: str) -> None:
    """Run Tier 0 anomaly detection on processed InSAR data."""
    from gews.detect import detect_anomalies
    from gews.process import InSARProcessor
    from gews.timeseries import compute_acceleration_map

    cfg = _load_config(config)

    # Load processed time series
    proc = InSARProcessor(cfg)
    ts = proc.load_timeseries()

    # Compute acceleration
    det_cfg = cfg["detect"]
    accel_map = compute_acceleration_map(
        ts.dates,
        ts.displacement,
        window_size_days=det_cfg["acceleration"]["window_size_days"],
        step_days=det_cfg["acceleration"]["step_days"],
        n_harmonics=det_cfg.get("n_harmonics", 2),
    )

    # Detect anomalies
    flags = detect_anomalies(accel_map, ts.latitude, ts.longitude, cfg)

    click.echo(f"\nTier 0 complete: {len(flags)} flags detected")
    for f in flags[:10]:
        click.echo(
            f"  Flag {f.flag_id}: score={f.score:.2f}, "
            f"z={f.peak_zscore:.1f}, "
            f"area={f.area_m2:,.0f} m², "
            f"({f.center_lat:.4f}°N, {f.center_lon:.4f}°E)"
        )


@main.command()
@click.option("--config", "-c", required=True, help="Path to site config YAML")
def assess(config: str) -> None:
    """Run Tier 1 cascade risk assessment."""
    click.echo("Tier 1 assessment requires DEM and population data.")
    click.echo("Use 'gews demo' for a complete demonstration with synthetic data.")


@main.command()
@click.option("--output", "-o", default="output", help="Output directory")
@click.option("--no-plots", is_flag=True, help="Skip generating plots")
def demo(output: str, no_plots: bool) -> None:
    """
    Run the full GEWS pipeline on synthetic data.

    Generates a realistic synthetic InSAR scene with an injected
    pre-failure acceleration signal, then runs Tier 0 detection
    and Tier 1 filtering. No real satellite data or processing
    tools required.

    This demonstrates the anomaly detection and cascade assessment
    algorithms on data mimicking the Nepal 2026 scenario.
    """
    from gews.detect import detect_anomalies
    from gews.report import generate_report
    from gews.synthetic import generate_synthetic_scene
    from gews.timeseries import compute_acceleration_map

    click.echo("=" * 60)
    click.echo("GEWS Demo — Synthetic Nepal 2026 Scenario")
    click.echo("=" * 60)
    click.echo()

    # Load default config for detection parameters
    config_path = Path(__file__).parent.parent.parent / "config" / "nepal_2026.yaml"
    if config_path.exists():
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
    else:
        # Minimal inline config
        cfg = {
            "site": {
                "name": "Nepal-Tibet Border 2026 (Synthetic)",
                "event_date": "2026-08-26",
            },
            "detect": {
                "n_harmonics": 2,
                "acceleration": {
                    "window_size_days": 60,
                    "step_days": 12,
                    "sigma_threshold": 2.5,
                    "changepoint": {
                        "model": "rbf",
                        "penalty": "bic",
                        "min_segment_size": 3,
                    },
                },
                "clustering": {
                    "min_cluster_pixels": 5,
                    "max_distance_m": 200,
                    "min_area_m2": 10000,
                },
                "voight": {
                    "enabled": True,
                    "min_points": 5,
                    "r_squared_threshold": 0.7,
                },
            },
        }
    cfg["site"]["name"] = "Nepal-Tibet Border 2026 (Synthetic Demo)"

    # Step 1: Generate synthetic data
    click.echo("Step 1/3  Generating synthetic InSAR data...")
    ts = generate_synthetic_scene()
    click.echo(
        f"  {len(ts.date_strings)} dates × "
        f"{ts.displacement.shape[1]}×{ts.displacement.shape[2]} pixels"
    )
    click.echo(
        f"  Date range: {ts.date_strings[0]} to {ts.date_strings[-1]}"
    )
    click.echo()

    # Step 2: Compute acceleration and detect anomalies
    click.echo("Step 2/3  Running anomaly detection...")
    det_cfg = cfg["detect"]
    accel_map = compute_acceleration_map(
        ts.dates,
        ts.displacement,
        window_size_days=det_cfg["acceleration"]["window_size_days"],
        step_days=det_cfg["acceleration"]["step_days"],
        n_harmonics=det_cfg.get("n_harmonics", 2),
    )

    flags = detect_anomalies(accel_map, ts.latitude, ts.longitude, cfg)
    click.echo(f"  Detected {len(flags)} anomaly flags")
    click.echo()

    # Step 3: Report
    if flags and not no_plots:
        click.echo("Step 3/3  Generating report...")
        report_path = generate_report(
            flags=flags,
            assessments=None,  # no DEM for cascade assessment in demo
            accel_map=accel_map,
            latitude=ts.latitude,
            longitude=ts.longitude,
            config=cfg,
            output_dir=output,
        )
        click.echo(f"  Report: {report_path}")
        click.echo(f"  Figures: {Path(output) / 'figures'}")
        click.echo(f"  GeoJSON: {Path(output) / 'flags.geojson'}")
    elif not flags:
        click.echo("  No flags detected (adjust sigma_threshold if needed)")
    else:
        click.echo("Step 3/3  Skipping plots (--no-plots)")

    # Summary
    click.echo()
    click.echo("=" * 60)
    click.echo("Results Summary")
    click.echo("=" * 60)

    if flags:
        click.echo(f"\n{'Flag':>6} {'Score':>7} {'Peak Z':>8} {'Area (m²)':>12} "
                    f"{'Accel (mm/yr²)':>16} {'Voight':>8}")
        click.echo("-" * 65)
        for f in flags[:10]:
            voight_str = (
                f"R²={f.voight_fit['r_squared']:.2f}"
                if f.voight_fit
                else "—"
            )
            click.echo(
                f"{f.flag_id:>6d} {f.score:>7.2f} {f.peak_zscore:>8.1f} "
                f"{f.area_m2:>12,.0f} "
                f"{f.acceleration_m_yr2 * 1000:>16.2f} "
                f"{voight_str:>8}"
            )

    click.echo()
    click.echo(
        "The injected failure zone was at row=80, col=120 "
        "(~28.22°N, 85.90°E)."
    )
    if flags:
        nearest = min(
            flags,
            key=lambda f: abs(f.center_lat - 28.22) + abs(f.center_lon - 85.90),
        )
        click.echo(
            f"Nearest detection: Flag {nearest.flag_id} at "
            f"{nearest.center_lat:.4f}°N, {nearest.center_lon:.4f}°E "
            f"(score={nearest.score:.2f})"
        )


@main.command()
@click.option("--config", "-c", required=True, help="Path to site/multi-site config YAML")
@click.option(
    "--interval",
    type=float,
    default=None,
    help="Check interval in hours (overrides config; default from config or 6)",
)
@click.option(
    "--check-now",
    is_flag=True,
    help="Run a single check cycle and exit (no loop)",
)
def monitor(config: str, interval: float | None, check_now: bool) -> None:
    """
    Run continuous monitoring for new NISAR acquisitions.

    Watches one or more sites for new data on ASF DAAC, downloads new
    scenes, runs the anomaly detection pipeline, and generates alerts.

    The config can be a single-site YAML (with a top-level 'site' key)
    or a multi-site YAML (with a 'sites' list and shared defaults).

    Examples:
        gews monitor -c config/nepal_2026.yaml --check-now
        gews monitor -c config/global_watch.yaml --interval 6
    """
    from gews.monitor import run_monitoring_loop, run_check_cycle, iter_site_configs

    # iter_site_configs and run_monitoring_loop take a file path,
    # not a loaded dict — they parse YAML internally.
    config_path = config

    if check_now:
        click.echo("Running single check cycle...")
        for site_cfg in iter_site_configs(config_path):
            site_name = site_cfg.get("site", {}).get("name", "unnamed")
            click.echo(f"\n{'─' * 50}")
            click.echo(f"Site: {site_name}")
            click.echo(f"{'─' * 50}")
            alerts = run_check_cycle(site_cfg)
            if alerts:
                for a in alerts:
                    level = a.get("level", "INFO")
                    msg = a.get("message", "")
                    click.echo(f"  [{level}] {msg}")
            else:
                click.echo("  No new alerts.")
        click.echo("\nDone.")
    else:
        cfg = _load_config(config)
        interval_hours = interval or cfg.get("monitor", {}).get("interval_hours", 6)
        click.echo(f"Starting monitoring loop (interval: {interval_hours}h)")
        click.echo(f"Press Ctrl+C to stop.\n")
        try:
            run_monitoring_loop(config_path, interval_hours=interval_hours)
        except KeyboardInterrupt:
            click.echo("\nMonitoring stopped.")


@main.command()
def version() -> None:
    """Show version information."""
    from gews import __version__

    click.echo(f"GEWS v{__version__}")
    click.echo("Glacier Early Warning System — PoC InSAR Pipeline")


if __name__ == "__main__":
    main()
