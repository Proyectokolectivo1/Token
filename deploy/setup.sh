#!/usr/bin/env bash
# statebate-pulse :: one-shot VM setup for Oracle Cloud Always Free (Ampere A1, Ubuntu ARM)
#
# Run this ON THE VM as the `ubuntu` user, after your first SSH login.
# It will: install deps, configure firewall, install docker, clone the repo,
# interactively collect secrets (no echo), write .env (gitignored, chmod 600),
# docker compose up, wait for DB healthy, and init the schema.
#
# Usage:
#   ssh ubuntu@<VM_PUBLIC_IP>
#   curl -fsSL https://raw.githubusercontent.com/Proyectokolectivo1/Token/main/deploy/setup.sh | bash
#   # OR, after cloning:
#   bash deploy/setup.sh
#
# Secrets (DB password, LLM key) are read interactively with `read -s` and
# written only to ./.env on the VM. They NEVER go to git.

set -euo pipefail

REPO_URL="https://github.com/Proyectokolectivo1/Token.git"
REPO_DIR="$HOME/Token"
DOMAIN_DEFAULT="pulse.local"

# --- logging helpers ---
log()   { printf "\n\033[1;36m▶ %s\033[0m\n" "$*"; }
ok()    { printf "\033[1;32m✓ %s\033[0m\n" "$*"; }
warn()  { printf "\033[1;33m! %s\033[0m\n" "$*"; }
err()   { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; }

# --- preflight ---
[ "$(whoami)" = "ubuntu" ] || { err "run as the 'ubuntu' user (not root)"; exit 1; }
if [ "$(uname -m)" = "aarch64" ]; then
  ok "ARM aarch64 detected (Always Free Ampere A1 eligible)"
else
  warn "non-ARM host ($(uname -m)); still works but may not be Always Free"
fi

log "1/7 — apt deps + firewall"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    git curl ufw fail2ban ca-certificates openssl
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
ok "firewall up (22, 80, 443)"

log "2/7 — docker"
if ! command -v docker &>/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
  ok "docker installed (group 'docker' added to $USER)"
  warn "if 'docker ps' fails with permission denied, run: newgrp docker  (then re-run this script)"
else
  ok "docker already installed"
fi

log "3/7 — clone / update repo"
if [ ! -d "$REPO_DIR" ]; then
  git clone "$REPO_URL" "$REPO_DIR"
fi
cd "$REPO_DIR"
git pull --rebase --autostash 2>/dev/null || true
ok "repo at $(pwd) @ $(git rev-parse --short HEAD)"

log "4/7 — collect secrets (input is HIDDEN, never echoed)"

read -rsp "DB password for user 'pulse': " DB_PWD; echo
[ -n "$DB_PWD" ] || { err "DB password is required"; exit 1; }
[ "${#DB_PWD}" -ge 8 ] || warn "short password (<8 chars) — PostgreSQL accepts it but consider stronger"

read -rp "Public domain (Enter for '$DOMAIN_DEFAULT'): " DOMAIN_IN
DOMAIN_IN="${DOMAIN_IN:-$DOMAIN_DEFAULT}"

read -rp "LLM provider [openai|groq|together|local] (default openai): " LLM_PROV
LLM_PROV="${LLM_PROV:-openai}"
read -rp "LLM model (default gpt-4o-mini): " LLM_MODEL_IN
LLM_MODEL_IN="${LLM_MODEL_IN:-gpt-4o-mini}"
read -rsp "LLM API key (Enter to skip; required for provider != local): " LLM_KEY; echo
read -rp "Boost affiliate ID (Enter to skip): " AFF_ID

PIXEL_SECRET_VAL="$(openssl rand -hex 24)"
API_KEY_VAL="$(openssl rand -hex 24)"

# Generate .env fresh (clean, no comments leaking structure).
# DATABASE_URL points to the docker service name 'db' (overridden by compose anyway).
cat > .env <<EOF
APP_ENV=production
APP_HOST=0.0.0.0
APP_PORT=8080
APP_LOG_LEVEL=info
APP_BASE_URL=https://${DOMAIN_IN}

DATABASE_URL=postgresql+asyncpg://pulse:${DB_PWD}@db:5432/pulse
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=5

DB_PASSWORD=${DB_PWD}
DOMAIN=${DOMAIN_IN}

LLM_PROVIDER=${LLM_PROV}
LLM_MODEL=${LLM_MODEL_IN}
LLM_API_KEY=${LLM_KEY}
LLM_BASE_URL=

TARGET_SITE=statebate.com
TARGET_BASE_URL=https://statebate.com
TARGET_ROOM_LIST_PATH=/list?page={page}
SCRAPE_REQUEST_DELAY=2.0
SCRAPE_CONCURRENCY=2
SCRAPE_USER_AGENT_ROTATION=true
SCRAPE_USE_PROXY=false

PIXEL_SECRET=${PIXEL_SECRET_VAL}
BOOST_AFFILIATE_ID=${AFF_ID}
BOOST_RECOMMENDER_LOOKBACK_DAYS=14

API_KEY=${API_KEY_VAL}
EOF
chmod 600 .env
ok ".env written (mode 600, gitignored)"

log "5/7 — docker compose up --build (db + app + caddy)"
if ! docker ps &>/dev/null; then
  err "docker not accessible. Run: newgrp docker   then re-run: bash deploy/setup.sh"
  exit 1
fi
docker compose up -d --build
ok "containers started"
docker compose ps

log "6/7 — wait for Postgres healthy (up to 60s)"
for i in $(seq 1 30); do
  if docker compose exec -T db pg_isready -U pulse >/dev/null 2>&1; then
    ok "db ready (after ${i}x2s)"
    break
  fi
  sleep 2
  if [ "$i" = "30" ]; then
    err "db not ready in 60s"
    docker compose logs db --tail 40
    exit 1
  fi
done

log "7/7 — initialize schema (tables, extensions, hypertables, materialized views)"
docker compose exec -T app python scripts/init_db.py
ok "schema initialized"

# --- final report ---
cat <<EOF

============================================================================
✅  statebate-pulse is UP
============================================================================

Endpoints:
  API health    https://${DOMAIN_IN}/api/healthz
  Pixel health  https://${DOMAIN_IN}/p/healthz
  API docs      https://${DOMAIN_IN}/api/docs   (disabled in production)

Admin API key (SAVE THIS — needed for /api/admin/* and GitHub Secrets):
  ${API_KEY_VAL}

First manual scrape (test the pipeline end-to-end):
  curl -X POST https://${DOMAIN_IN}/api/admin/scrape/once \\
    -H "X-Api-Key: ${API_KEY_VAL}" \\
    -H "Content-Type: application/json" -d '{}'

GitHub Secrets to set (repo → Settings → Secrets and variables → Actions):
  PULSE_API_URL  =  https://${DOMAIN_IN}
  PULSE_API_KEY  =  ${API_KEY_VAL}

Useful commands:
  Logs          docker compose logs -f app caddy
  DB shell      docker compose exec db psql -U pulse -d pulse
  List tables   docker compose exec db psql -U pulse -d pulse -c "\\dt"
  Stop          docker compose down
  Restart app   docker compose restart app

Next:
  1. Point your DNS A record → this VM's public IP.
  2. Set the GitHub Secrets above so the scrape cron + Pages deploy work.
  3. Activate GitHub Pages (repo → Settings → Pages → Source: GitHub Actions).

============================================================================
EOF
