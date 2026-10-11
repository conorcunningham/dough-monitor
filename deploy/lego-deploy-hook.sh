#!/usr/bin/env bash
# Run by lego after it obtains or renews the certificate (--deploy-hook).
# Copies the certificate where Caddy can read it and reloads Caddy.
# lego 5 sets LEGO_HOOK_CERT_NAME, LEGO_HOOK_CERT_PATH and LEGO_HOOK_CERT_KEY_PATH.
set -euo pipefail

dest=/etc/caddy/certs
install -d -m 750 -o root -g caddy "$dest"
install -m 644 -o root -g caddy "$LEGO_HOOK_CERT_PATH" "$dest/$LEGO_HOOK_CERT_NAME.crt"
install -m 640 -o root -g caddy "$LEGO_HOOK_CERT_KEY_PATH" "$dest/$LEGO_HOOK_CERT_NAME.key"
systemctl try-reload-or-restart caddy
echo "Installed certificate for $LEGO_HOOK_CERT_NAME into $dest and reloaded Caddy."
