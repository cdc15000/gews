# GEWS — Global Early Warning System

Proof-of-concept InSAR pipeline for satellite detection of unstable glaciers and rock slopes.

GEWS screens displacement time series derived from satellite radar (Sentinel-1 C-band and NISAR L-band) for anomalous acceleration — the signal that precedes catastrophic glacier and rock-slope collapses. It implements the Tier 0 (automated screening) and Tier 1 (cascade risk filtering) stages described in the [GEWS technical proposal](https://claude.ai/code/artifact/02bb2a36-8c0d-4af9-b80d-1349ed9f4ad8), plus an operational `gews monitor` mode that continuously watches configured sites for new acquisitions and raises alerts.

## Key findings — Nepal 2026 case study

Retrospective analysis of the August 26, 2026 Nepal–Tibet border glacier–rock collapse against archived NISAR data validated the detection approach against a real event:

- **43 days of advance notice** — the sigma-threshold acceleration-anomaly detector flagged the site well ahead of the collapse, even though Voight's inverse-velocity law failed to converge for this event (the failure was a two-phase step-change, not smooth pre-failure acceleration).
- **5.06 m of vertical subsidence** measured in the lead-up to collapse, decomposed from line-of-sight displacement using the site's local incidence geometry.
- **295 anomaly flags** from NISAR GOFF (amplitude offset-tracking) detection, which stayed coherent through the meter-scale displacement that saturated phase-based (GUNW) InSAR.

These results are what motivated the NISAR loaders (`nisar.py`) and the operational monitoring loop (`monitor.py`): GUNW phase unwrapping is limited to about half a wavelength (~12 cm/cycle for L-band) per interval, so large, fast-moving pre-collapse displacement is only observable via GOFF offset tracking.

## Quick start

### Synthetic demo (no satellite data required)

Run the full pipeline on synthetic data mimicking the Nepal 2026 glacier–rock collapse, with no satellite data or InSAR processing tools required:

```bash
# Set up environment
python -m venv .venv
source .venv/bin/activate
pip install -e .

# Run the demo
gews demo --output output
```

This generates a synthetic InSAR scene with an injected pre-failure acceleration signal, runs anomaly detection, and produces:

- **`output/report.md`** — Summary of detected anomalies
- **`output/flags.geojson`** — Flagged sites for viewing in QGIS / Google Earth
- **`output/figures/anomaly_map.png`** — Spatial acceleration z-score map
- **`output/figures/flag_*_timeseries.png`** — Time-series plots per flagged site

### Operational monitoring (real NISAR data)

Once an [Earthdata](#data-requirements) account and `.netrc` are configured, watch one or more real sites for new NISAR acquisitions:

```bash
# Single check cycle against the built-in global watchlist, then exit
gews monitor -c config/global_watch.yaml --check-now

# Continuous loop, checking every 6 hours (or per config)
gews monitor -c config/global_watch.yaml --interval 6
```

Each cycle searches ASF DAAC for new GUNW/GOFF products, downloads anything not already seen, re-runs the detection pipeline on the grown time series, and writes any new alerts to `output/alerts/<site>_alerts.json`. State (which products have been seen, which anomalies already alerted on) is checkpointed to `data/monitor_state/` so a restart resumes rather than re-alerting.

## Architecture

```
Sentinel-1 SLC data         NISAR GUNW / GOFF products
        │                              │
        ▼                              ▼
┌─── acquire.py ───┐          ┌──── nisar.py ─────┐
│ Search & download │          │ Load GUNW/GOFF     │
│ from ASF DAAC      │          │ HDF5, SBAS-invert  │
└────────────────────┘          │ to displacement,   │
        │                       │ estimate velocity  │
        ▼                       └────────────────────┘
┌─── process.py ───┐                      │
│ ISCE-2 → MintPy    │                     │
│ time-series        │                     │
│ inversion          │                     │
└────────────────────┘                     │
        │                                  │
        └──────────────┬───────────────────┘
                        ▼
              ┌── timeseries.py ─┐   Seasonal decomposition, acceleration
              │                  │   estimation, sliding-window z-scores,
              └──────────────────┘   Voight's law fitting
                        │
                        ▼
              ┌─── detect.py ────┐   Tier 0: Spatial clustering of
              │                  │   anomalous pixels, deduplication,
              └──────────────────┘   scoring, ranking
                        │
             ┌──────────┴───────────┐
             ▼                      ▼
   ┌─── cascade.py ───┐   ┌──── monitor.py ────┐
   │ Tier 1: volume,   │   │ Continuous watch:   │
   │ valley, runout,   │   │ checks for new data, │
   │ population        │   │ tracks state,        │
   │ exposure          │   │ raises alerts         │
   └───────────────────┘   └───────────────────────┘
             │
             ▼
   ┌─── report.py ────┐   Maps, time-series plots, GeoJSON, Markdown report
   └──────────────────┘
```

## Pipeline stages

### 1. Data acquisition (`gews search`, `gews download`)

Searches the ASF DAAC archive for Sentinel-1 SLC scenes covering a study area, selects the optimal orbital track (maximizing temporal sampling), and downloads scenes. Requires an [Earthdata Login](https://urs.earthdata.nasa.gov/) account.

```bash
gews search --config config/nepal_2026.yaml
gews download --config config/nepal_2026.yaml
```

### 2. InSAR processing (`gews process`)

Wraps ISCE-2 (interferogram generation) and MintPy (time-series inversion) to produce displacement time series from the downloaded SLC data. Generates configuration files and orchestrates the processing workflow.

**Prerequisites:** ISCE-2 and MintPy must be installed separately. See [ISCE-2](https://github.com/isce-framework/isce2) and [MintPy](https://github.com/insarlab/MintPy) documentation.

```bash
gews process --config config/nepal_2026.yaml
gews process --config config/nepal_2026.yaml --step prepare  # just generate configs
gews process --config config/nepal_2026.yaml --dry-run       # print commands only
```

### 3. Anomaly detection (`gews detect`)

The core analytical component. For each pixel:
1. **Decomposes** the displacement time series into linear trend + seasonal harmonics + residual
2. **Estimates acceleration** via sliding-window velocity regression on the residuals
3. **Normalizes** each pixel's acceleration against its own historical baseline (z-score)
4. **Clusters** spatially connected anomalous pixels using DBSCAN
5. **Scores and ranks** clusters by composite anomaly score
6. Optionally fits **Voight's failure law** (inverse-velocity trend) to top candidates

No labeled collapse data is required — each site is compared to its own history.

### 4. Cascade risk assessment (`gews assess`)

Evaluates whether flagged sites can produce dangerous downstream cascades:
- **Volume estimation** from deformation extent and slope geometry
- **Valley confinement analysis** from DEM
- **Empirical runout modeling** (angle-of-reach)
- **Population exposure** from WorldPop or similar data

### 5. NISAR data loading (`nisar.py`)

Reads NISAR L2 GUNW (unwrapped interferogram) and GOFF (pixel offset) HDF5 products and inverts them into the same `displacement`/`velocity` time-series representation used everywhere else in the pipeline, via a vectorized SBAS (small baseline subset) least-squares inversion. NISAR's L-band (24 cm) wavelength maintains coherence on glaciated and heavily deforming surfaces where Sentinel-1's C-band (5.6 cm) decorrelates; GOFF amplitude offset-tracking further extends coverage to meter-scale displacements that saturate phase-based unwrapping entirely.

### 6. Operational monitoring (`gews monitor`)

Turns the retrospective pipeline into a standing watch: `monitor.py` periodically searches ASF DAAC for new NISAR products at each configured site, downloads anything new, re-runs detection on the grown product set, and raises leveled alerts (`INFO`/`WARNING`/`CRITICAL`) for anomalies not already alerted on. Accepts either a single-site config (like `config/nepal_2026.yaml`) or a multi-site watchlist (like `config/global_watch.yaml`) with shared defaults merged per-site. State is checkpointed to disk so restarts resume cleanly.

```bash
gews monitor -c config/nepal_2026.yaml --check-now      # single site, one pass
gews monitor -c config/global_watch.yaml --interval 6   # multi-site, every 6h
```

## Configuration

Site-specific parameters are defined in YAML files under `config/`. See `config/nepal_2026.yaml` for a fully documented example covering:

- Study area coordinates and buffer
- Sentinel-1 search parameters (date range, polarization, track)
- InSAR processing settings (DEM source, network method, unwrapping)
- Detection thresholds (z-score sigma, minimum cluster size, Voight parameters)
- Cascade risk filters (minimum volume, angle of reach, valley width)

## Key algorithms

### Acceleration z-score

Rather than applying a global threshold ("any glacier moving faster than X is dangerous"), GEWS compares each site to its own historical behavior. A glacier that routinely accelerates every summer is not flagged for its summer acceleration — only when its acceleration exceeds its own historical summer range.

The z-score is computed as:

```
z(t) = (a(t) - μ_baseline) / σ_baseline
```

where `a(t)` is the acceleration at time `t`, and `μ_baseline`, `σ_baseline` are the mean and standard deviation of acceleration over the site's baseline period (first 70% of the observation window).

### Voight's failure law

For top-scoring anomalies, GEWS fits Voight's empirical relation, which predicts that the inverse of velocity (1/v) decreases linearly toward zero as catastrophic failure approaches. Originally developed for volcanic eruption forecasting (Voight 1988), it has been applied to landslide and glacier collapse prediction. The x-intercept of the linear fit gives a predicted failure time.

## Running with real data

To analyze a real study area:

1. **Create a site config** based on `config/nepal_2026.yaml`
2. **Search and download data:** `gews search -c your_config.yaml` then `gews download`
3. **Process with ISCE-2 + MintPy:** `gews process -c your_config.yaml` (requires these tools installed)
4. **Run detection:** `gews detect -c your_config.yaml`
5. **Review results** in the output directory

Or, to run continuously against real NISAR data instead of a one-off batch: `gews monitor -c your_config.yaml --check-now` (see [Operational monitoring](#operational-monitoring-real-nisar-data) above).

## Data requirements

Real (non-synthetic) data access — `gews search`, `gews download`, and `gews monitor` — requires a free [NASA Earthdata Login](https://urs.earthdata.nasa.gov/) account, since both Sentinel-1 (via ASF DAAC) and NISAR products are distributed through Earthdata-authenticated endpoints.

Configure credentials once via a `.netrc` file in your home directory (`~/.netrc`, permissions `600`):

```
machine urs.earthdata.nasa.gov
    login <your-earthdata-username>
    password <your-earthdata-password>
```

`asf_search` and the NISAR download path both read this file automatically — no credentials are read from or stored in this repository, and none should ever be committed to it. Do not put real credentials in config YAML files under `config/`.

Additional data needed for full-pipeline runs:

- **Sentinel-1 SLC** or **NISAR GUNW/GOFF** products — fetched via `gews search`/`gews download`/`gews monitor`, or supplied directly under `data/slc` / `data/nisar/{gunw,goff}`.
- **DEM** (for Tier 1 cascade assessment) — any DEM covering the study area (e.g. Copernicus GLO-30), referenced in the site config.
- **Population data** (optional, for exposure estimates) — e.g. WorldPop raster covering the study area.

The synthetic demo (`gews demo`) requires none of the above.

## Docker

A `Dockerfile` is provided for a reproducible, dependency-pinned environment:

```bash
docker build -t gews .

# Synthetic demo — no credentials or volumes needed
docker run --rm -v "$PWD/output:/app/output" gews demo --output /app/output

# Real monitoring — mount credentials, config, and persistent data/output dirs
docker run --rm \
    -v "$HOME/.netrc:/root/.netrc:ro" \
    -v "$PWD/config:/app/config:ro" \
    -v "$PWD/data:/app/data" \
    -v "$PWD/output:/app/output" \
    gews monitor -c config/global_watch.yaml --check-now
```

The image's `ENTRYPOINT` is the `gews` CLI, so any `gews` subcommand can follow `docker run --rm gews ...` directly.

## Development

```bash
pip install -e ".[dev]"
pytest                     # run tests
pytest -v --tb=short       # verbose with short tracebacks
```

All tests use synthetic data and run without network access or real satellite products.

## License

MIT (see `pyproject.toml`). A standalone `LICENSE` file has not yet been added — this is a proof-of-concept research project.

## References

- Wang, W. et al. "Early warning system for glacial lake outburst floods in Cirenmaco, Tibet." *Int. J. Disaster Risk Reduct.* 73, 102914 (2022).
- Voight, B. "A method for prediction of volcanic eruptions." *Nature* 332, 125–130 (1988).
- Shirzaei, M. "Satellite images before Nepal disaster showed warning signs." *Nature* News Q&A, 2 September 2026.
- Rosen, P.A. et al. "An InSAR time series approach." *J. Geophys. Res.* (2004). [ISCE-2 framework]
- Yunjun, Z. et al. "Small baseline InSAR time series analysis." *Comput. Geosci.* (2019). [MintPy]
