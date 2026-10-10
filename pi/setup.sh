#!/usr/bin/env bash
# Set up a Raspberry Pi as the Dough Monitor sensor node: the Mosquitto broker,
# rtl_433 for the 433 MHz room sensor and the DS18B20 probe publisher.
#
# Idempotent: re-run it after changing anything in pi/ and it only touches what
# differs, restarting just the affected service. The dongle and the probe can be
# plugged in before or after; the services pick them up by themselves.
#
# On the Pi, from a checkout of this repo or any copy of the pi/ directory:
#   sudo pi/setup.sh
# From your laptop, which copies pi/ over and runs this there, then check.sh:
#   pi/bootstrap.sh conor@doughie.local
set -euo pipefail
# A laptop's ssh forwards its locale; the Pi may not have it, and apt's perl helpers complain.
export LC_ALL=C.UTF-8

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
config_txt=/boot/firmware/config.txt
[[ -f $config_txt ]] || config_txt=/boot/config.txt # Raspberry Pi OS before bookworm

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo: sudo $0" >&2
    exit 1
fi

step() { printf '\n==> %s\n' "$*"; }

# put SRC DEST MODE: install SRC at DEST unless it is already identical.
# Succeeds when it changed something, fails when there was nothing to do.
put() {
    local src=$1 dest=$2 mode=$3
    if [[ -f $dest ]] && cmp -s "$src" "$dest"; then
        return 1
    fi
    install -D -m "$mode" "$src" "$dest"
    echo "installed $dest"
}

step "Checking the OS"
. /etc/os-release
echo "$PRETTY_NAME on $(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo 'unknown board')"
if ((${VERSION_ID:-0} < 13)); then
    echo "Warning: this release ships a librtlsdr that predates the RTL-SDR Blog V4 dongle." >&2
    echo "         rtl_433 will run but hear nothing from a V4. Use Raspberry Pi OS trixie or newer." >&2
fi

step "Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q mosquitto mosquitto-clients rtl-433 rtl-sdr python3-paho-mqtt

step "MQTT broker"
systemctl enable -q mosquitto
if put "$here/mosquitto-lan.conf" /etc/mosquitto/conf.d/lan.conf 644; then
    systemctl restart mosquitto
fi
systemctl start mosquitto

step "rtl_433 for the room sensor"
if put "$here/rtl-sdr-blacklist.conf" /etc/modprobe.d/rtl-sdr-blacklist.conf 644; then
    # The blacklist counts at the next boot; drop the driver now if the kernel already has the dongle.
    modprobe -r dvb_usb_rtl28xxu 2>/dev/null || true
fi
# librtlsdr0's udev rule opens the dongle to the plugdev group; apply it to a dongle plugged in before the install.
udevadm control --reload
udevadm trigger --subsystem-match=usb --attr-match=idVendor=0bda
restart_rtl=0
if put "$here/rtl_433.conf" /etc/rtl_433/rtl_433.conf 644; then restart_rtl=1; fi
if put "$here/rtl_433.service" /etc/systemd/system/rtl_433.service 644; then restart_rtl=1; fi
systemctl daemon-reload
systemctl enable -q rtl_433
if ((restart_rtl)) || ! systemctl is-active -q rtl_433; then
    systemctl restart rtl_433
fi

step "1-Wire bus for the dough probe"
if ! grep -qE '^\s*dtoverlay=w1-gpio\b' "$config_txt"; then
    # The stock config.txt ends in an [all] section; make sure the line lands in one.
    if [[ $(grep -E '^\[' "$config_txt" | tail -1) != '[all]' ]]; then
        printf '\n[all]\n' >>"$config_txt"
    fi
    echo 'dtoverlay=w1-gpio' >>"$config_txt"
    echo "added dtoverlay=w1-gpio to $config_txt"
fi
if [[ ! -d /sys/bus/w1 ]]; then
    # config.txt only counts at boot; load the overlay now so no reboot is needed.
    dtoverlay w1-gpio && echo "loaded the w1-gpio overlay" || echo "could not load the overlay now; it will load at the next boot"
fi

step "DS18B20 probe publisher"
restart_probe=0
if put "$here/ds18b20_publisher.py" /usr/local/bin/ds18b20_publisher.py 755; then restart_probe=1; fi
if put "$here/dough-probe.service" /etc/systemd/system/dough-probe.service 644; then restart_probe=1; fi
systemctl daemon-reload
systemctl enable -q dough-probe
if ((restart_probe)) || ! systemctl is-active -q dough-probe; then
    systemctl restart dough-probe
fi

step "Done"
sleep 2 # let the services settle before reporting
ip=$(hostname -I | awk '{print $1}')
printf '%-12s %s\n' "host" "$(hostname) ($ip)"
for unit in mosquitto rtl_433 dough-probe; do
    printf '%-12s %s\n' "$unit" "$(systemctl is-active "$unit")"
done
if grep -qs '^0bda$' /sys/bus/usb/devices/*/idVendor; then
    printf '%-12s %s\n' "RTL-SDR" "plugged in"
else
    printf '%-12s %s\n' "RTL-SDR" "not plugged in (rtl_433 retries every 10 s until it is)"
fi
probes=""
for probe in /sys/bus/w1/devices/28-*; do
    [[ -e $probe ]] && probes+="${probe##*/} "
done
printf '%-12s %s\n' "DS18B20" "${probes:-none found yet (the bus rescans every 10 s; check the wiring)}"
cat <<EOF

Next:
  * on the app host, set MQTT_HOST=$ip in .env and restart 'manage.py mqtt_listen'
  * run pi/check.sh here: it tests the whole chain and prints the sensor IDs to register
EOF
