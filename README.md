# Dough Monitor

Tracks the temperature of bread dough against the temperature of the room it is rising in, so you can see what the dough actually did rather than what the recipe said it would.

A Raspberry Pi reads two sensors and publishes them over MQTT:

- a waterproof DS18B20 probe in the dough, wired to the Pi's GPIO
- a cheap 433 MHz temperature/humidity sensor for the room, picked up by an RTL-SDR dongle and decoded by [rtl_433](https://github.com/merbanan/rtl_433)

A Django app listens to the broker, stores the readings in Postgres and shows the current dough with a live chart. Mixing, folds, fermentation, shaping and proofing are recorded with buttons as they happen.

```
DS18B20 probe ─1-Wire─▶ ds18b20_publisher.py ─┐
                                              ├─▶ Mosquitto ─▶ manage.py mqtt_listen ─▶ Postgres ─▶ Django + htmx
433 MHz sensor ─radio─▶ RTL-SDR ─▶ rtl_433 ───┘
└──────────────── Raspberry Pi ───────────────┘   └──────────────── laptop or server ────────────────┘
```

## Raspberry Pi setup

Flash Raspberry Pi OS Lite (trixie or newer) with Raspberry Pi Imager, turning on SSH and setting your user in its settings. Everything the Pi needs is a standard package plus the files in `pi/`, and `pi/setup.sh` installs all of it. From your laptop:

```
pi/bootstrap.sh conor@doughie.local
```

That copies `pi/` to the Pi, runs `setup.sh` there with sudo and finishes with `check.sh`, which tests every link in the chain and shows the probe's ID and the 433 MHz traffic it hears, ready to register. Both are safe to re-run: `setup.sh` only changes what differs and restarts only the service concerned, so it is also how changes to `pi/` get rolled out. Alternatively clone this repo on the Pi and run `sudo pi/setup.sh` there.

The rest of this section is what the script does, for when something needs a closer look.

### MQTT broker

```
sudo apt install mosquitto mosquitto-clients
```

Mosquitto 2 only listens on localhost until you give it a listener. `pi/mosquitto-lan.conf`, installed as `/etc/mosquitto/conf.d/lan.conf`, opens it to the LAN without a password and raises the per-client queue so a night's readings survive the app being offline:

```
listener 1883
allow_anonymous true
max_queued_messages 20000
```

then `sudo systemctl restart mosquitto`. That is fine on a home LAN. Add a password file (and set `MQTT_USERNAME`/`MQTT_PASSWORD` in `.env`) before exposing the broker any further.

### Room sensor: RTL-SDR and rtl_433

```
sudo apt install rtl-433 rtl-sdr
```

Installing `rtl-433` pulls in `librtlsdr0` and the udev rule that lets the `plugdev` group use the dongle. The kernel still claims it as a DVB-T receiver at boot; rtl_433 detaches it, but it is cleaner to keep the kernel off it altogether, which `pi/rtl-sdr-blacklist.conf` does once copied to `/etc/modprobe.d/`. Unplug and re-plug the dongle (or reboot), then check it is seen:

```
rtl_test -t
```

An RTL-SDR Blog V4 needs `librtlsdr` 2.0 or newer, which Raspberry Pi OS trixie ships. If `rtl_test` prints "RTL-SDR Blog V4 Detected" you are set. If it only reports a generic R828D tuner, the driver predates the V4 (bookworm and older) and rtl_433 will run but hear nothing: update the OS rather than building the [rtl-sdr.com](https://www.rtl-sdr.com/V4) driver, which replaces `librtlsdr0` and takes the `rtl-433` package with it.

Now listen for the sensor. Put it within a metre of the dongle and you should see an event within a minute or two (most sensors transmit every 30–60 s) even with no antenna attached. If you need more range, a bare wire about 17 cm long pushed into the centre of the SMA socket is a serviceable quarter-wave antenna for 433 MHz.

```
rtl_433 -F json -M level
```

Each transmission prints one JSON line, with exactly the field names the app sees:

```
{"time" : "2026-10-09 12:00:00", "model" : "Nexus-TH", "id" : 42, "channel" : 1, "battery_ok" : 1, "temperature_C" : 21.3, "humidity" : 55, "mic" : "CHECKSUM", "rssi" : -0.1, "snr" : 20.5, "noise" : -20.6}
```

The sensor's hardware ID in the app is `model/channel/id`, so `Nexus-TH/1/42` here (just `model/id` if there is no channel). You will see your neighbours' sensors and the odd passing car's tyre-pressure sensor too; yours is the one with `rssi` near 0 and the highest `snr`. The app ignores anything you have not registered. Some sensors repeat each reading two or three times per transmission; the app stores one.

Once it is hearing the sensor, run it as a service with the config in `pi/rtl_433.conf`, which publishes to the broker with timestamps that carry their UTC offset. The unit runs as a throwaway system user in the `plugdev` group, so it needs no editing:

```
sudo install -D -m 644 pi/rtl_433.conf /etc/rtl_433/rtl_433.conf
sudo cp pi/rtl_433.service /etc/systemd/system/
sudo systemctl enable --now rtl_433
mosquitto_sub -t 'rtl_433/#' -v        # one event per transmission
```

While the service runs it owns the dongle, so a by-hand `rtl_test` or `rtl_433` fails with `usb_claim_interface error -6`. Stop it with `sudo systemctl stop rtl_433` for manual tests and start it again afterwards.

### Dough probe: DS18B20

The waterproof probe has three leads. Colours vary by vendor, so check the listing, but usually red is power, black (sometimes blue) is ground and yellow (sometimes white) is data. On a breadboard, with male-to-female jumpers to the Pi header:

| Probe lead     | Pi header pin  |
| -------------- | -------------- |
| red, power     | pin 1, 3.3 V   |
| black, ground  | pin 6, GND     |
| yellow, data   | pin 7, GPIO 4  |

plus a 4.7 kΩ resistor (yellow-violet-red) between the data row and the 3.3 V row. Use 3.3 V, not 5 V: the pull-up would put 5 V on a GPIO pin.

Enable 1-Wire by adding `dtoverlay=w1-gpio` to `/boot/firmware/config.txt` for every boot from now on, and `sudo dtoverlay w1-gpio` to load it right away without rebooting. The probe then shows up as a directory named after its ROM ID, which is also its hardware ID in the app:

```
ls /sys/bus/w1/devices/                  # 28-0123456789ab  w1_bus_master1
cat /sys/bus/w1/devices/28-*/w1_slave    # ... crc=57 YES / ... t=23125
```

`t=23125` is 23.125 °C. If the first line ends in `NO`, or only `w1_bus_master1` is listed, recheck the wiring and the resistor. Pinch the probe tip and the number should climb.

Install the publisher. Like the rtl_433 unit it runs as a throwaway system user, so nothing needs editing:

```
sudo apt install python3-paho-mqtt
sudo install -m 755 pi/ds18b20_publisher.py /usr/local/bin/
sudo cp pi/dough-probe.service /etc/systemd/system/
sudo systemctl enable --now dough-probe
mosquitto_sub -t 'dough-monitor/#' -v    # a reading every 30 s
```

The script's docstring lists the environment variables it reads; set them with `Environment=` lines in the unit.

## Running the app

Requires Python 3.14, [uv](https://docs.astral.sh/uv/) and Postgres 16+.

```
cp .env.example .env        # then set the secret key, DATABASE_URL and MQTT_HOST
uv sync
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

In a second terminal start the MQTT listener. It runs until stopped and reconnects by itself; while it is off, the broker queues readings for it.

```
uv run python manage.py mqtt_listen
```

### Running it as a service

For an always-on install (a second Pi, or any Linux box on the LAN) `deploy/` has systemd units for the web app (gunicorn, static files via WhiteNoise) and the MQTT listener, plus an idempotent installer. On that machine:

```
sudo apt install postgresql                      # 16+; Debian 13 ships 17
sudo -u postgres psql -c "CREATE ROLE dough_monitor WITH LOGIN CREATEDB PASSWORD '...';" \
                      -c "CREATE DATABASE dough_monitor OWNER dough_monitor;"
git clone https://github.com/conorcunningham/dough-monitor.git && cd dough-monitor
cp .env.example .env                             # DJANGO_DEBUG=False, your hostname in DJANGO_ALLOWED_HOSTS,
                                                 # MQTT_HOST pointing at the sensor Pi, a unique MQTT_CLIENT_ID
deploy/install.sh
```

Gunicorn listens on `127.0.0.1:8000` only; HTTPS is added below. Re-run `deploy/install.sh` after a `git pull` to roll out a new version; logs are in `journalctl -u dough-monitor-web -u dough-monitor-mqtt -f`. Create your admin user with `uv run python manage.py createsuperuser` in the checkout.

### HTTPS with a real certificate

[Caddy](https://caddyserver.com/) sits in front of gunicorn as the web server and TLS terminator, and [lego](https://go-acme.github.io/lego/) obtains and renews a Let's Encrypt certificate using a DNS-01 challenge against the [Domeneshop](https://domene.shop/) API. DNS-01 only needs a public TXT record, so the box can stay on the LAN; the hostname itself only has to resolve locally (Pi-hole, `/etc/hosts`, ...). If your DNS is elsewhere, swap `domeneshop` in `deploy/lego-renew.service` for one of lego's other providers.

1. Create an API key and secret in the Domeneshop control panel (`https://www.domeneshop.no/admin?view=api`) and store them on the server, readable by root only:

   ```
   sudo mkdir -p /etc/caddy
   sudo nano /etc/caddy/domeneshop.env     # DOMENESHOP_API_TOKEN=... and DOMENESHOP_API_SECRET=...
   sudo chmod 600 /etc/caddy/domeneshop.env
   ```

2. Add the hostname to `DJANGO_ALLOWED_HOSTS` and set `DJANGO_CSRF_TRUSTED_ORIGINS=https://<hostname>` in `.env`, then re-run `deploy/install.sh`.

3. Install Caddy, lego and stubby (a DNS-over-TLS stub so lego can see the public DNS even when the LAN resolver claims the domain), fetch the certificate and start the daily renewal timer:

   ```
   sudo deploy/install-tls.sh <hostname> <your email for Let's Encrypt notices>
   ```

The app is then at `https://<hostname>/`, with HTTP redirected to HTTPS. The certificate lives in `/var/lib/lego` and is copied to `/etc/caddy/certs/` by `deploy/lego-deploy-hook.sh`, which also reloads Caddy; `systemctl list-timers lego-renew.timer` shows the next renewal check and `journalctl -u lego-renew -u caddy` the logs. Re-running `deploy/install-tls.sh` is safe and picks up changes to the Caddyfile or units.

### Registering sensors

Readings are only stored for sensors you have registered. When the listener hears one it does not know, it logs the ID once:

```
INFO sensors.mqtt: Ignoring unregistered sensor 'Nexus-TH/1/42' on rtl_433/raspberrypi/events (21.3 °C). Add it in the admin to record it.
```

Add it at `http://localhost:8000/admin/sensors/sensor/` with that hardware ID, a name and a kind (ambient for the room sensor, dough for the probe). No restart needed. If a battery change gives a 433 MHz sensor a new ID, edit the hardware ID on the existing sensor rather than creating a new one, so its history stays attached.

Registering a sensor is not enough on its own: a reading is only stored while a dough is collecting from that sensor (as its dough or ambient sensor, from "Start fermentation" until "Start shaping"), judged by the reading's own timestamp so readings the broker queued overnight still land in the right window. Between doughs the listener drops readings and says so at DEBUG level. The **Readings** page in the nav shows how many readings each sensor has, how many fall outside every dough's collection window (from before this rule, or from a deleted dough), and a button to delete those stray readings.

### Smoke test without the hardware

From the Pi, or anywhere with `mosquitto_pub`, publish a fake rtl_433 event and watch the listener log it:

```
mosquitto_pub -h raspberrypi.local -t rtl_433/test/events \
  -m '{"time":"2026-10-09T12:00:00+0200","model":"Nexus-TH","id":42,"channel":1,"temperature_C":21.3,"humidity":55}'
```

## Tests

```
uv run python manage.py test --parallel
```

The database user needs `CREATEDB` so the parallel runner can create its test databases.
