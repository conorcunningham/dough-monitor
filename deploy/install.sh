#!/usr/bin/env bash
# Install or update the Dough Monitor app on the server it runs on (a Pi 5 or any Linux box).
#
# Run from a checkout of this repo on that machine, as the user that owns the checkout:
#   deploy/install.sh
#
# Expects: uv on PATH, a Postgres role and database matching DATABASE_URL, and a filled-in .env
# (see .env.example). Idempotent: re-run after `git pull` to roll out a new version.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"

step() { printf '\n==> %s\n' "$*"; }

[[ -f .env ]] || { echo "No .env in $here; copy .env.example and fill it in first." >&2; exit 1; }
command -v uv >/dev/null || { echo "uv not found on PATH (https://docs.astral.sh/uv/)." >&2; exit 1; }

step "Installing Python dependencies"
uv sync --no-dev

step "Migrating the database"
uv run python manage.py migrate --no-input

step "Collecting static files"
uv run python manage.py collectstatic --no-input --clear | tail -1

step "Installing systemd units"
for unit in dough-monitor-web dough-monitor-mqtt; do
    # Point the unit at this checkout and user rather than the committed defaults.
    sed -e "s|^User=.*|User=$(id -un)|" \
        -e "s|/home/conor/dough-monitor|$here|g" \
        "deploy/$unit.service" | sudo tee "/etc/systemd/system/$unit.service" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now dough-monitor-web dough-monitor-mqtt
sudo systemctl restart dough-monitor-web dough-monitor-mqtt

step "Status"
systemctl --no-pager --lines=0 status dough-monitor-web dough-monitor-mqtt | grep -E "●|Active:"
echo
echo "Web app: http://$(hostname -f 2>/dev/null || hostname):8000/"
echo "Logs:    journalctl -u dough-monitor-web -u dough-monitor-mqtt -f"
