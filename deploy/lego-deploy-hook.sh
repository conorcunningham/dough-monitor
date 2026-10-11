#!/usr/bin/env bash
# Run by lego after it obtains or renews the certificate (--deploy-hook).
# Copies the certificate where Caddy can read it and reloads Caddy.
# lego sets LEGO_CERT_DOMAIN, LEGO_CERT_PATH and LEGO_CERT_KEY_PATH.
set -euo pipefail

dest=/etc/caddy/certs
install -d -m 750 -o root -g caddy "$dest"
install -m 644 -o root -g caddy "$LEGO_CERT_PATH" "$dest/$LEGO_CERT_DOMAIN.crt"
install -m 640 -o root -g caddy "$LEGO_CERT_KEY_PATH" "$dest/$LEGO_CERT_DOMAIN.key"
systemctl try-reload-or-restart caddy
echo "Installed certificate for $LEGO_CERT_DOMAIN into $dest and reloaded Caddy."
