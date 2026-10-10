#!/usr/bin/env bash
# Set up a Raspberry Pi from your laptop: copies this directory to the Pi over
# SSH, runs setup.sh there with sudo, then check.sh.
#
#   pi/bootstrap.sh conor@doughie.local
#
# The Pi needs SSH enabled (tick it in Raspberry Pi Imager) and a user that can
# sudo. The files go to ~/dough-monitor-pi on the Pi, deliberately outside any
# git checkout there. Re-run it to roll out changes to pi/.
set -euo pipefail

target=${1:?usage: pi/bootstrap.sh user@host}
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
remote=dough-monitor-pi

echo "==> Copying pi/ to $target:~/$remote"
ssh "$target" "mkdir -p $remote"
scp -q "$here"/*.sh "$here"/*.conf "$here"/*.service "$here"/ds18b20_publisher.py "$target:$remote/"

echo "==> Running setup.sh on $target"
ssh -t "$target" "sudo bash $remote/setup.sh && bash $remote/check.sh"
