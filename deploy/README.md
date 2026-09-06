# GEWS Deployment Guide

## Prerequisites

- **Docker** >= 20.10 and **Docker Compose** >= 2.0 (or `docker-compose` v1.29+)
- An **Earthdata** account (free — register at <https://urs.earthdata.nasa.gov>)
- Earthdata credentials stored in `~/.netrc`:
  ```
  machine urs.earthdata.nasa.gov
      login YOUR_USERNAME
      password YOUR_PASSWORD
  ```

## Quick Start

```bash
cd /path/to/gews

# Build the images
docker compose build

# Start both monitor and dashboard
docker compose up -d

# View logs
docker compose logs -f monitor
```

The dashboard is available at <http://localhost:8080> by default.

## Configuration

All settings can be controlled with environment variables, either exported in
your shell or placed in a `.env` file alongside `docker-compose.yml`.

| Variable                | Default                      | Description                            |
|------------------------|------------------------------|----------------------------------------|
| `GEWS_CONFIG`          | `config/global_watch.yaml`   | Site config file (relative to /app)    |
| `GEWS_INTERVAL`        | `6`                          | Check interval in hours                |
| `GEWS_DASHBOARD_PORT`  | `8080`                       | Port the dashboard listens on          |
| `GEWS_SMTP_HOST`       | *(empty)*                    | SMTP server for email alerts           |
| `GEWS_SMTP_PORT`       | `587`                        | SMTP port                              |
| `GEWS_SMTP_USER`       | *(empty)*                    | SMTP username                          |
| `GEWS_SMTP_PASS`       | *(empty)*                    | SMTP password / app password           |
| `GEWS_SMTP_TO`         | *(empty)*                    | Alert recipient email(s)               |
| `GEWS_SLACK_WEBHOOK`   | *(empty)*                    | Slack incoming-webhook URL             |
| `GEWS_WEBHOOK_URL`     | *(empty)*                    | Generic HTTP webhook for alerts        |

### Using an Override File

For persistent customizations, copy the example override:

```bash
cp docker-compose.override.yml.example docker-compose.override.yml
```

Edit the override file with your credentials and preferences.  Docker Compose
merges it with the base file automatically.

## Setting Up Alerts

### Email (SMTP)

Set the `GEWS_SMTP_*` variables.  For Gmail, create an App Password
(Account > Security > App Passwords) and use it as `GEWS_SMTP_PASS`:

```bash
export GEWS_SMTP_HOST=smtp.gmail.com
export GEWS_SMTP_PORT=587
export GEWS_SMTP_USER=you@gmail.com
export GEWS_SMTP_PASS=xxxx-xxxx-xxxx-xxxx
export GEWS_SMTP_TO=team@example.com
```

### Slack

Create an incoming webhook in your Slack workspace
(<https://api.slack.com/messaging/webhooks>) and set:

```bash
export GEWS_SLACK_WEBHOOK=https://hooks.slack.com/services/T00/B00/xxxx
```

### Generic Webhook

Point `GEWS_WEBHOOK_URL` at any HTTP endpoint.  GEWS POSTs a JSON payload
with alert details on each trigger.

## Monitoring Multiple Sites

Use `config/global_watch.yaml` (the default) which defines a `sites` list.
Add entries to that file, or point `GEWS_CONFIG` at your own multi-site YAML:

```yaml
# my_watchlist.yaml
defaults:
  acquire:
    platform: SENTINEL-1
monitor:
  interval_hours: 4
sites:
  - !include nepal_2026.yaml
  - !include weisshorn_2026.yaml
```

## Viewing the Dashboard

Open `http://<host>:8080` (or the port you configured).  The dashboard reads
from the shared `output/` volume and updates as the monitor produces new
results.

## Log Access and Troubleshooting

```bash
# Follow all logs
docker compose logs -f

# Monitor service only
docker compose logs -f monitor

# Check container health
docker compose ps

# Restart a single service
docker compose restart monitor

# Rebuild after code changes
docker compose build && docker compose up -d
```

Data is persisted in Docker named volumes (`gews-data`, `gews-output`).
To inspect them directly:

```bash
docker volume inspect gews_gews-output
```

## Running Without Docker

### systemd (Linux)

A unit file is provided at `deploy/systemd/gews-monitor.service`.  Install it:

```bash
sudo cp deploy/systemd/gews-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gews-monitor
```

Edit the unit file first to set `WorkingDirectory`, `User`, and the path to
your config.

### cron

A crontab entry is provided at `deploy/cron/gews-check.cron`.  Install it:

```bash
crontab -l | cat - deploy/cron/gews-check.cron | crontab -
```

This runs a single check cycle every 6 hours rather than a persistent process.

## Running on a Cloud VM

### AWS EC2

1. Launch an instance (t3.medium or larger; 20 GB+ EBS).
2. Install Docker: `sudo yum install docker && sudo systemctl enable --now docker`.
3. Install Docker Compose plugin.
4. Clone the repo, create `.env` with your credentials, and run `docker compose up -d`.
5. Open port 8080 in the security group to access the dashboard.

### GCP Compute Engine

1. Create a VM (e2-medium or larger; 20 GB persistent disk).
2. Install Docker: `sudo apt-get install docker.io docker-compose-plugin`.
3. Clone, configure, and run as above.
4. Add a firewall rule allowing TCP 8080.

For both providers, consider placing the dashboard behind a reverse proxy
(nginx, Caddy) with TLS if exposing it beyond your network.
