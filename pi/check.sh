#!/usr/bin/env bash
# Check a Pi set up by pi/setup.sh, end to end: packages, services, the broker's
# LAN listener, the dongle, the 1-Wire probe, and that readings from both sensors
# actually arrive on the broker. Read-only, no sudo needed. Prints one line per
# check and exits non-zero if any failed.
#   pi/check.sh
set -uo pipefail
export LC_ALL=C.UTF-8

failed=0
pass() { printf 'ok    %s\n' "$*"; }
warn() { printf 'warn  %s\n' "$*"; }
skip() { printf 'skip  %s\n' "$*"; }
fail() {
    printf 'FAIL  %s\n' "$*"
    failed=1
}

config_txt=/boot/firmware/config.txt
[[ -f $config_txt ]] || config_txt=/boot/config.txt
ip=$(hostname -I | awk '{print $1}')
dongle=0
grep -qs '^0bda$' /sys/bus/usb/devices/*/idVendor && dongle=1

. /etc/os-release
if ((${VERSION_ID:-0} >= 13)); then
    pass "$PRETTY_NAME"
else
    fail "$PRETTY_NAME is too old for an RTL-SDR Blog V4 dongle; use trixie or newer"
fi

missing=""
for pkg in mosquitto mosquitto-clients rtl-433 rtl-sdr python3-paho-mqtt; do
    dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q 'install ok installed' || missing+="$pkg "
done
if [[ -n $missing ]]; then fail "packages missing: $missing"; else pass "packages installed"; fi

for unit in mosquitto rtl_433 dough-probe; do
    if ! systemctl is-active -q "$unit"; then
        if [[ $unit == rtl_433 ]] && ((!dongle)); then
            fail "rtl_433 is waiting for a dongle (none on USB); it retries every 10 s"
        else
            fail "$unit is not running: journalctl -u $unit -n 20"
        fi
    elif ! systemctl is-enabled -q "$unit"; then
        warn "$unit is running but will not start at boot"
    else
        pass "$unit running and enabled"
    fi
done

if version=$(mosquitto_sub -h "$ip" -t '$SYS/broker/version' -C 1 -W 5 2>/dev/null); then
    pass "broker accepts LAN connections on $ip:1883 ($version)"
else
    fail "broker not reachable on $ip:1883 (lan.conf missing, or mosquitto not restarted)"
fi

if ((dongle)); then
    pass "RTL-SDR dongle on USB"
else
    fail "no RTL-SDR dongle on USB"
fi
if [[ -f /etc/modprobe.d/rtl-sdr-blacklist.conf ]]; then
    pass "kernel DVB driver blacklisted"
else
    warn "kernel DVB driver not blacklisted; rtl_433 detaches it, but run setup.sh"
fi
log=$(journalctl -u rtl_433 -b --no-pager -o cat 2>/dev/null)
if ((!dongle)); then
    skip "rtl_433 log: no dongle"
elif [[ -z $log ]]; then
    warn "no rtl_433 log for this boot (reading the journal needs the adm group or sudo)"
elif grep -q 'RTL-SDR Blog V4 Detected' <<<"$log"; then
    pass "rtl_433 detected the RTL-SDR Blog V4"
elif grep -qE 'Found .* tuner' <<<"$log"; then
    pass "rtl_433 opened the dongle ($(grep -oE 'Found .* tuner' <<<"$log" | tail -1))"
elif grep -q 'usb_claim_interface error' <<<"$log"; then
    fail "rtl_433 cannot claim the dongle: something else holds it"
elif grep -qE 'No supported devices found|usb_open error' <<<"$log"; then
    fail "rtl_433 finds no dongle"
else
    warn "rtl_433 has not reported a tuner yet"
fi

overlays=$(grep -cE '^\s*dtoverlay=w1-gpio\b' "$config_txt")
if ((overlays == 0)); then
    fail "dtoverlay=w1-gpio missing from $config_txt (the bus will be gone after a reboot)"
elif ((overlays > 1)); then
    warn "dtoverlay=w1-gpio appears $overlays times in $config_txt; keep one"
else
    pass "w1-gpio overlay in $config_txt"
fi
found=0
if [[ ! -d /sys/bus/w1/devices ]]; then
    fail "no 1-Wire bus (overlay not loaded: run setup.sh or reboot)"
else
    for probe in /sys/bus/w1/devices/28-*; do
        [[ -e $probe ]] || continue
        found=1
        reading=$(cat "$probe/w1_slave" 2>/dev/null)
        millidegrees=$(sed -n 's/.*t=//p' <<<"$reading")
        if ! grep -q 'YES' <<<"$reading"; then
            fail "probe ${probe##*/} fails its CRC check (bad contact or missing pull-up resistor)"
        elif [[ $millidegrees == 85000 ]]; then
            warn "probe ${probe##*/} reads 85 °C, its power-on value: the power lead is probably loose"
        else
            pass "probe ${probe##*/} reads $(awk "BEGIN { printf \"%.3f\", $millidegrees / 1000 }") °C"
        fi
    done
    ((found)) || fail "no DS18B20 on the bus (check the wiring; the bus rescans every 10 s)"
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
((dongle || found)) && echo "      waiting up to 70 s for readings to arrive on the broker..."
((dongle)) && mosquitto_sub -t 'rtl_433/+/events' -C 1 -W 70 >"$tmp/event" 2>/dev/null &
((found)) && mosquitto_sub -t 'dough-monitor/#' -C 1 -W 40 >"$tmp/probe" 2>/dev/null &
wait
if ((!dongle)); then
    skip "433 MHz traffic: no dongle"
elif [[ -s $tmp/event ]]; then
    model=$(grep -oE '"model":"[^"]*"' "$tmp/event" | cut -d'"' -f4)
    channel=$(grep -oE '"channel":"?[^,}"]*' "$tmp/event" | sed 's/.*://; s/"//')
    id=$(grep -oE '"id":[^,}]*' "$tmp/event" | cut -d: -f2)
    temperature=$(grep -oE '"temperature_C":[^,}]*' "$tmp/event" | cut -d: -f2)
    pass "433 MHz events reach the broker, e.g. ${model}${channel:+/$channel}${id:+/$id} at ${temperature:-?} °C"
else
    fail "no 433 MHz event on the broker within 70 s (antenna on? sensor in range?)"
fi
if ((!found)); then
    skip "probe traffic: no probe"
elif [[ -s $tmp/probe ]]; then
    pass "probe readings reach the broker: $(head -c 120 "$tmp/probe")"
else
    fail "no probe reading on the broker within 40 s: journalctl -u dough-probe -n 20"
fi

if grep -qE "[[:space:]]$(hostname)([[:space:]]|$)" /etc/hosts; then
    pass "hostname $(hostname) is in /etc/hosts"
else
    warn "hostname $(hostname) is missing from /etc/hosts, so sudo will complain"
fi

echo
if ((failed)); then
    echo "Some checks failed."
    exit 1
fi
echo "All good. The app host needs MQTT_HOST=$ip in its .env, and the sensor IDs above registered in the admin."
