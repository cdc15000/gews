# Glacier Early Warning System — Deployment Guide

Production deployment options for the GEWS monitoring pipeline and analyst dashboard.

---

## Prerequisites

- **Docker** >= 20.10 and **Docker Compose** >= 2.0
- A free [NASA Earthdata](https://urs.earthdata.nasa.gov/) account for real satellite data
- Earthdata credentials in `~/.netrc`:
  ```
  machine urs.earthdata.nasa.gov
      login YOUR_USERNAME
      password YOUR_PASSWORD
  ```

---

## Docker Quickstart

Build the GEWS image and run the synthetic demo to verify the installation:

```bash
# Build
docker build -t gews .

# Verify with the synthetic demo (no credentials needed)
docker run --rm -v "$PWD/output:/app/output" gews demo --output /app/output

# Run a single monitoring check against real data
docker run --rm \
    -v "$HOME/.netrc:/root/.netrc:ro" \
    -v "$PWD/config:/app/config:ro" \
    -v "$PWD/data:/app/data" \
    -v "$PWD/output:/app/output" \
    gews monitor -c config/global_watch.yaml --check-now
```

The image's `ENTRYPOINT` is the `gews` CLI, so any subcommand follows `docker run --rm gews ...` directly.

---

## Docker Compose

The recommended deployment for most installations. Starts both the monitoring loop and the analyst dashboard as long-running services.

```bash
cd /path/to/gews

# Build images
docker compose build

# Start services in the background
docker compose up -d

# View monitor logs
docker compose logs -f monitor

# Stop
docker compose down
```

The dashboard is available at `http://localhost:8080` by default (configurable via `GEWS_DASHBOARD_PORT`).

### docker-compose.yml overview

```yaml
services:
  monitor:
    build: .
    command: monitor -c ${GEWS_CONFIG:-config/global_watch.yaml} --interval ${GEWS_INTERVAL:-6}
    volumes:
      - ${HOME}/.netrc:/root/.netrc:ro
      - ./config:/app/config:ro
      - ./data:/app/data
      - ./output:/app/output
    environment:
      - GEWS_SMTP_HOST
      - GEWS_SMTP_PORT
      - GEWS_SMTP_USER
      - GEWS_SMTP_PASS
      - GEWS_SMTP_TO
      - GEWS_SLACK_WEBHOOK
      - GEWS_WEBHOOK_URL
    restart: unless-stopped

  dashboard:
    build: .
    command: dashboard -c ${GEWS_CONFIG:-config/global_watch.yaml} --data-dir /app/output --port 8080
    ports:
      - "${GEWS_DASHBOARD_PORT:-8080}:8080"
    volumes:
      - ./config:/app/config:ro
      - ./output:/app/output:ro
    restart: unless-stopped
```

### Override file

For persistent customizations without modifying the base compose file:

```bash
cp docker-compose.override.yml.example docker-compose.override.yml
# Edit with your credentials and preferences
```

Docker Compose merges the override file automatically.

---

## Environment Variables

All deployment settings are controlled via environment variables, either exported in the shell or placed in a `.env` file alongside `docker-compose.yml`.

### Core settings

| Variable | Default | Description |
|----------|---------|-------------|
| `GEWS_CONFIG` | `config/global_watch.yaml` | Site config file (relative to /app) |
| `GEWS_INTERVAL` | `6` | Check interval in hours |
| `GEWS_DASHBOARD_PORT` | `8080` | Dashboard listen port |

### Email alerts (SMTP)

| Variable | Default | Description |
|----------|---------|-------------|
| `GEWS_SMTP_HOST` | *(empty)* | SMTP server hostname |
| `GEWS_SMTP_PORT` | `587` | SMTP port (587 for STARTTLS) |
| `GEWS_SMTP_USER` | *(empty)* | SMTP username |
| `GEWS_SMTP_PASS` | *(empty)* | SMTP password or app password |
| `GEWS_SMTP_TO` | *(empty)* | Alert recipient email(s), comma-separated |

For Gmail, create an App Password (Account > Security > App Passwords) and use it as `GEWS_SMTP_PASS`:

```bash
export GEWS_SMTP_HOST=smtp.gmail.com
export GEWS_SMTP_PORT=587
export GEWS_SMTP_USER=you@gmail.com
export GEWS_SMTP_PASS=xxxx-xxxx-xxxx-xxxx
export GEWS_SMTP_TO=team@example.com
```

### Slack alerts

| Variable | Default | Description |
|----------|---------|-------------|
| `GEWS_SLACK_WEBHOOK` | *(empty)* | Slack incoming-webhook URL |

Create an incoming webhook at <https://api.slack.com/messaging/webhooks>.

### Generic webhook

| Variable | Default | Description |
|----------|---------|-------------|
| `GEWS_WEBHOOK_URL` | *(empty)* | HTTP endpoint for JSON POST alerts |

Alert payloads are JSON objects with `level`, `site`, `summary`, `details`, and `timestamp` fields.

---

## Kubernetes

Kubernetes manifests are provided in `deploy/kubernetes/`. These define a CronJob for periodic monitoring checks and a Deployment for the dashboard.

### Deploying

```bash
# Create namespace
kubectl create namespace gews

# Create secrets for Earthdata and alert credentials
kubectl -n gews create secret generic gews-earthdata \
    --from-file=netrc=$HOME/.netrc

kubectl -n gews create secret generic gews-alerts \
    --from-literal=GEWS_SMTP_HOST=smtp.gmail.com \
    --from-literal=GEWS_SMTP_PORT=587 \
    --from-literal=GEWS_SMTP_USER=you@gmail.com \
    --from-literal=GEWS_SMTP_PASS=xxxx-xxxx-xxxx-xxxx \
    --from-literal=GEWS_SMTP_TO=team@example.com \
    --from-literal=GEWS_SLACK_WEBHOOK=https://hooks.slack.com/...

# Create config map from your site config
kubectl -n gews create configmap gews-config \
    --from-file=global_watch.yaml=config/global_watch.yaml

# Apply manifests
kubectl -n gews apply -f deploy/kubernetes/

# Check status
kubectl -n gews get cronjobs,deployments,pods
```

### Key resources

- **CronJob `gews-monitor`** — runs `gews monitor --check-now` every 6 hours (configurable via the schedule field). Uses a PersistentVolumeClaim for data and output directories so state persists across runs.
- **Deployment `gews-dashboard`** — serves the analyst dashboard. Reads from the same output PVC.
- **PersistentVolumeClaim `gews-data`** — shared storage for downloaded products, monitor state, and detection output.

### Scaling considerations

- The monitor CronJob should have `concurrencyPolicy: Forbid` to prevent overlapping check cycles.
- For multi-site watchlists with many sites, increase the CronJob's `activeDeadlineSeconds` to accommodate longer check cycles.
- The dashboard is stateless and can be scaled horizontally if needed, though a single replica is sufficient for most deployments.

---

## systemd

A systemd unit file is provided at `deploy/systemd/gews-monitor.service` for running the Glacier Early Warning System monitor as a Linux service.

### Installation

```bash
# Copy the unit file
sudo cp deploy/systemd/gews-monitor.service /etc/systemd/system/

# Edit to set your paths and environment
sudo systemctl edit gews-monitor.service

# Reload, enable, and start
sudo systemctl daemon-reload
sudo systemctl enable gews-monitor.service
sudo systemctl start gews-monitor.service

# Check status and logs
sudo systemctl status gews-monitor.service
journalctl -u gews-monitor.service -f
```

### Unit file overview

```ini
[Unit]
Description=GEWS Glacier Early Warning System Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=gews
WorkingDirectory=/opt/gews
ExecStart=/opt/gews/.venv/bin/gews monitor -c config/global_watch.yaml --interval 6
Restart=on-failure
RestartSec=60
EnvironmentFile=/opt/gews/.env

[Install]
WantedBy=multi-user.target
```

Place alert credentials in `/opt/gews/.env` (permissions `600`):

```bash
GEWS_SMTP_HOST=smtp.gmail.com
GEWS_SMTP_PORT=587
GEWS_SMTP_USER=you@gmail.com
GEWS_SMTP_PASS=xxxx-xxxx-xxxx-xxxx
GEWS_SMTP_TO=team@example.com
GEWS_SLACK_WEBHOOK=https://hooks.slack.com/...
```

---

## cron

For environments where a persistent daemon is not desired, a crontab entry runs single-pass check cycles on a schedule.

### Installation

```bash
# Install from the provided crontab fragment
crontab -l | cat - deploy/cron/gews-check.cron | crontab -

# Or add manually (every 6 hours):
crontab -e
```

### Crontab entry

```
# GEWS monitoring check — every 6 hours
0 */6 * * * cd /opt/gews && /opt/gews/.venv/bin/gews monitor -c config/global_watch.yaml --check-now >> /var/log/gews/monitor.log 2>&1
```

The `--check-now` flag runs a single check cycle and exits. State is checkpointed to `data/monitor_state/`, so each run resumes from where the previous one left off.

### Log rotation

```bash
# /etc/logrotate.d/gews
/var/log/gews/*.log {
    weekly
    rotate 12
    compress
    missingok
    notifempty
}
```

---

## Monitoring and Health Checks

Configuration for operational monitoring is in `deploy/monitoring/`.

### Health check endpoint

The dashboard serves a health check at `/health` that returns HTTP 200 with a JSON body:

```json
{
  "status": "ok",
  "last_check": "2026-08-15T12:00:00Z",
  "sites_monitored": 13,
  "active_alerts": 2
}
```

### Prometheus metrics

If Prometheus scraping is configured, key metrics to watch:

| Metric | Description |
|--------|-------------|
| `gews_check_cycle_duration_seconds` | Time for a complete check cycle |
| `gews_products_downloaded_total` | Cumulative NISAR products downloaded |
| `gews_anomaly_flags_total` | Total anomaly flags raised |
| `gews_alerts_dispatched_total` | Alerts sent (by channel and level) |
| `gews_check_cycle_errors_total` | Failed check cycles |

### Alerting on the alerter

To ensure the monitoring system itself is healthy:

1. **Process monitoring** — systemd's `Restart=on-failure` or Kubernetes liveness probes handle process crashes.
2. **Heartbeat** — configure a dead-man's switch (e.g., Healthchecks.io, PagerDuty heartbeat) that the monitor pings after each successful check cycle. If no ping arrives within `2 * interval`, the monitor is presumed down.
3. **Log monitoring** — watch for `ERROR` lines in the monitor log. Repeated `ERROR: download failed` or `ERROR: ASF search failed` indicates an upstream data-access problem.

---

## Terraform

Infrastructure-as-code templates are provided in `deploy/terraform/` for cloud deployments. These are reference configurations — adapt to your cloud environment and security requirements.

---

## Security Notes

- **Never commit credentials** to config files or the repository. Use `${ENV_VAR}` expansion in YAML configs and environment variables or `.env` files for deployment.
- The `~/.netrc` file should have permissions `600` and be mounted read-only in containers.
- The dashboard does not implement authentication. In production, place it behind a reverse proxy (e.g., nginx, Traefik) with appropriate access controls.
- Alert webhook URLs and SMTP credentials should be treated as secrets and managed accordingly (Kubernetes Secrets, systemd EnvironmentFile with restricted permissions, etc.).
