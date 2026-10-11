#!/usr/bin/env bash
# Put HTTPS in front of the Dough Monitor web app: Caddy as the web server and reverse proxy,
# lego to obtain and renew a Let's Encrypt certificate with a DNS-01 challenge via the
# Domeneshop API. DNS-01 means the host never has to be reachable from the internet.
#
# Run from a checkout of this repo on the server, as root:
#   sudo deploy/install-tls.sh HOSTNAME EMAIL
# HOSTNAME is the name on the certificate (a record under a domain hosted at Domeneshop) and
# EMAIL is the Let's Encrypt account contact for expiry warnings.
#
# Expects /etc/caddy/domeneshop.env, readable by root only, containing
#   DOMENESHOP_API_TOKEN=...
#   DOMENESHOP_API_SECRET=...
# (created at https://www.domeneshop.no/admin?view=api). Idempotent: re-run after `git pull`.
set -euo pipefail

host="${1:?usage: sudo deploy/install-tls.sh HOSTNAME EMAIL}"
email="${2:?usage: sudo deploy/install-tls.sh HOSTNAME EMAIL}"
creds=/etc/caddy/domeneshop.env
lego_version=5.5.2
hook_dir=/usr/local/lib/dough-monitor

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"

step() { printf '\n==> %s\n' "$*"; }

[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }

step "Installing Caddy and stubby"
# stubby is a DNS-over-TLS stub resolver on 127.0.0.1:53. lego uses it to work out which zone
# the hostname belongs to, because a LAN resolver that answers for the domain itself (Pi-hole
# with a local search domain) hides the public SOA/NS records, and some routers intercept all
# port 53 traffic so pointing lego at 1.1.1.1 directly does not help either.
apt-get install -y caddy stubby
# /etc/caddy may pre-exist with mode 700 (created by hand for domeneshop.env); Caddy runs as
# its own user and must be able to read the Caddyfile.
chmod 755 /etc/caddy
systemctl enable --now stubby
caddy version

step "Checking Domeneshop credentials in $creds"
[[ -f $creds ]] || { echo "Missing $creds; see the header of this script." >&2; exit 1; }
chmod 600 "$creds"
grep -q '^DOMENESHOP_API_TOKEN=.' "$creds" && grep -q '^DOMENESHOP_API_SECRET=.' "$creds" \
    || { echo "$creds must set DOMENESHOP_API_TOKEN and DOMENESHOP_API_SECRET." >&2; exit 1; }

step "Installing lego $lego_version"
case "$(dpkg --print-architecture)" in
    arm64) arch=arm64; sha256=15b14ec2ab14fde69cc8396eb0204c5ce4327e31a486953225a6059b26db3e8c ;;
    amd64) arch=amd64; sha256=2a35505089e7772c92e1e9ac144df91151ef2eca8568630db0ff91fca06d9bef ;;
    *) echo "No lego checksum pinned for $(dpkg --print-architecture); add one to this script." >&2; exit 1 ;;
esac
if [[ "$(/usr/local/bin/lego --version 2>/dev/null || true)" != *"$lego_version"* ]]; then
    tmp="$(mktemp -d)"
    tarball="lego_v${lego_version}_linux_${arch}.tar.gz"
    curl -fsSL -o "$tmp/$tarball" \
        "https://github.com/go-acme/lego/releases/download/v${lego_version}/${tarball}"
    echo "$sha256  $tmp/$tarball" | sha256sum -c -
    tar -xzf "$tmp/$tarball" -C "$tmp" lego
    install -m 755 "$tmp/lego" /usr/local/bin/lego
    rm -rf "$tmp"
fi
/usr/local/bin/lego --version

step "Installing the certificate deploy hook and renewal timer"
install -d -m 755 "$hook_dir"
install -m 755 deploy/lego-deploy-hook.sh "$hook_dir/lego-deploy-hook.sh"
sed -e "s|__HOST__|$host|g" -e "s|__EMAIL__|$email|g" deploy/lego-renew.service \
    > /etc/systemd/system/lego-renew.service
install -m 644 deploy/lego-renew.timer /etc/systemd/system/lego-renew.timer
systemctl daemon-reload
systemctl enable --now lego-renew.timer

cert_store=/var/lib/lego/certificates
if [[ ! -f /etc/caddy/certs/$host.crt ]]; then
    if [[ -f $cert_store/$host.crt ]]; then
        # Issued earlier but never handed to Caddy (say the hook failed): no need to ask the CA again.
        step "Installing the existing certificate for Caddy"
        LEGO_HOOK_CERT_NAME="$host" LEGO_HOOK_CERT_PATH="$cert_store/$host.crt" \
            LEGO_HOOK_CERT_KEY_PATH="$cert_store/$host.key" "$hook_dir/lego-deploy-hook.sh"
    else
        step "Obtaining the first certificate for $host (DNS-01 via Domeneshop)"
        systemctl start lego-renew.service
    fi
fi

step "Installing the Caddyfile"
sed "s|__HOST__|$host|g" deploy/Caddyfile > /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile >/dev/null
systemctl enable --now caddy
systemctl reload caddy

step "Status"
systemctl --no-pager --lines=0 status caddy lego-renew.timer | grep -E "●|Active:"
echo
echo "Web app: https://$host/"
echo "Make sure $host resolves to this machine on your network (Pi-hole local DNS, /etc/hosts, ...)."
echo "Renewal: systemctl list-timers lego-renew.timer; journalctl -u lego-renew -u caddy"
