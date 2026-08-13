#!/usr/bin/env bash
# SkyNYC — deploy the working tree to the server and start the stack (guide 3 §A).
#
# Works against a root login (classic VPS providers) or a sudo-capable user —
# AWS Ubuntu AMIs log in as `ubuntu`. First run on a fresh box: installs Docker
# via get.docker.com, adds a 4 GB swap file, syncs the tree, brings up infra,
# creates topics, starts ingestion, applies migrations, starts Spark + Dagster.
# Re-runs just sync + restart what changed. The laptop keeps nothing running.
#
# Prereqs in .env: VPS_HOST (e.g. ubuntu@<ec2-ip> or root@<ip>), VPS_SSH_KEY.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
set -a; source "$REPO_ROOT/.env"; set +a

: "${VPS_HOST:?set VPS_HOST in .env (e.g. ubuntu@203.0.113.7)}"
KEY="${VPS_SSH_KEY/#\~/$HOME}"
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new "$VPS_HOST")
DEST="skynyc"

# root on classic VPSes, sudo on cloud AMIs — detect once, prefix everywhere.
RSUDO=$("${SSH[@]}" 'if [ "$(id -u)" -eq 0 ]; then echo ""; else echo sudo; fi')

echo "==> docker present on $VPS_HOST?"
if ! "${SSH[@]}" 'command -v docker >/dev/null'; then
  echo "==> installing docker (get.docker.com) + 4G swap"
  "${SSH[@]}" 'curl -fsSL https://get.docker.com | sh'  # script self-elevates via sudo
  # 8 GB box + a 4 GB Spark driver (M2): swap is the safety margin, not the plan.
  "${SSH[@]}" "test -f /swapfile || ($RSUDO fallocate -l 4G /swapfile \
    && $RSUDO chmod 600 /swapfile && $RSUDO mkswap /swapfile && $RSUDO swapon /swapfile \
    && echo '/swapfile none swap sw 0 0' | $RSUDO tee -a /etc/fstab >/dev/null)"
fi

# Fresh Ubuntu images ship without make; the Makefile is the operational interface.
"${SSH[@]}" "command -v make >/dev/null || $RSUDO apt-get install -y make"

echo "==> sync working tree -> $VPS_HOST:~/$DEST (includes .env — the box needs the secrets)"
rsync -az --delete -e "ssh -i $KEY -o StrictHostKeyChecking=accept-new" \
  --exclude .git --exclude data/ --exclude checkpoints/ --exclude backups/ \
  --exclude __pycache__ --exclude .venv --exclude .pytest_cache --exclude .DS_Store \
  "$REPO_ROOT/" "$VPS_HOST:~/$DEST/"

echo "==> up + topics + ingest"
"${SSH[@]}" "cd $DEST && $RSUDO docker compose up -d kafka postgres grafana \
  && sleep 20 && $RSUDO make topics && $RSUDO make ingest"

# 001 runs via initdb.d only on an empty volume (Manual 05); the later numbered
# files are written idempotent, so re-applying them on every deploy is safe.
echo "==> migrations"
"${SSH[@]}" "cd $DEST && for f in db/init/0*.sql; do \
  [ \"\$f\" = db/init/001_schema.sql ] && continue; \
  $RSUDO docker compose exec -T postgres psql -U skynyc -d skynyc < \"\$f\" || exit 1; done"

# Public-API role password lives only in .env (public-dashboard spec §3.1); set it
# server-side on every deploy so rotation is just an .env edit + re-deploy. The
# firewall opens exactly the two ports Caddy publishes — the one loopback exception.
echo "==> public api role + firewall"
"${SSH[@]}" "cd $DEST && set -a && . ./.env && set +a; \
  if [ -n \"\${PG_RO_PASSWORD:-}\" ]; then \
    printf \"ALTER ROLE skynyc_ro PASSWORD '%s';\" \"\$PG_RO_PASSWORD\" \
      | $RSUDO docker compose exec -T postgres psql -U skynyc -d skynyc -q; \
  else echo 'PG_RO_PASSWORD unset — skipping role password'; fi"
"${SSH[@]}" "$RSUDO ufw allow 80/tcp >/dev/null 2>&1; $RSUDO ufw allow 443/tcp >/dev/null 2>&1; true"

echo "==> stream + batch"
"${SSH[@]}" "cd $DEST && $RSUDO make stream && $RSUDO make batch"

echo "==> verify"
"${SSH[@]}" "cd $DEST && $RSUDO docker compose ps --format 'table {{.Service}}\t{{.Status}}' \
  && sleep 35 && $RSUDO docker compose logs --no-log-prefix --tail 2 producer-opensky \
  && $RSUDO docker compose ps --format 'table {{.Service}}\t{{.Status}}' spark dagster-daemon"

echo
echo "deployed. Grafana stays private — reach it with a tunnel, never an open port:"
echo "  ssh -i $KEY -N -L 3000:localhost:3000 $VPS_HOST   # then http://localhost:3000"
