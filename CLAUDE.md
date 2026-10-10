# Overview

This tool is to fetch temperature readings from two sensors: one in a dough and the other recording the ambient temperature. These values will be tracked against a Dough model. It records real-world data, not plans: anything that varies day to day (fermentation, proofing) is captured with start/stop buttons, not typed in.

# Data Model

## Sensor
- Hardware ID (e.g. DS18B20 ROM ID `28-xxxxxxxxxxxx`, or the rtl_433 model+id)
  - Must be editable: many 433 MHz sensors change their ID on battery swap, and history must survive that.
- Name
- Kind: dough or ambient

## Reading
- Sensor
- Recorded at (timestamp from the Pi, not time of receipt)
- Temperature (Celsius)
- Humidity (nullable; only the RTL-SDR sensor provides it)

Readings are stored independently. They are NOT linked to a dough by foreign key; a dough's readings are its sensors' readings within its collection window.

## Dough
Not all fields are mandatory at creation. Clicking "create" is enough.

- Dough sensor and ambient sensor (several doughs may share an ambient sensor; multiple doughs/sensors can run simultaneously)
- Flour (grams)
- Water (grams)
- Water Temperature (Celsius)
- Yeast (milligrams integer but displayed as grams)
- Salt (grams)
- Start time (now, on creation): readings collection starts
- Collection end: set by a "Start Shaping" (stop collection) button, or automatically when a new dough is created on the same dough sensor
- Folds (one to many): a timestamp each
- Fermentation: started/ended timestamps via start/stop buttons; duration (hours) is derived
- Proofing: started/ended timestamps via start/stop buttons; duration (minutes) is derived
- Baking temp: pre-planned, default 250 C
- Lid on dutch oven duration: pre-planned, default 30 minutes
- Lid off dutch oven duration: pre-planned, default 20 minutes
- Notes
- Rating (1 - 5)
- Rise (low, medium, full, excellent)

Hydration and salt % are derived for display, not stored.

## README.md
We will work together to develop this readme for public consumption. It will become a public repo.

# Framework

## Django Backend
Django is the framework of choice

## HTMX GUI
HTMX will do the GUI, we don't need to go full React or Vue here. The main view is the current dough with a live-updating chart.

- htmx, Chart.js and Pico CSS are vendored in `static/vendor/` (versions in the file names) so the app works on a LAN without CDN access.
- Action buttons are real `<form method="post">`s with `hx-post`: htmx requests get a partial back, plain posts redirect.
- Run the web app with `uv run python manage.py runserver`.

## Database
Postgres 16+

## Configuration
All secrets and environment-specific settings come from environment variables (via `django-environ`), loaded from a gitignored `.env`. `.env.example` documents every variable. Never hard-code secrets.

# Getting the data
The data will be fetched using MQTT. MQTT is push-based, so this is a long-running listener (a management command), not a scheduled poll. Periodic jobs are only for things like "no reading from a sensor in N minutes" alerts.

- Run it with `uv run python manage.py mqtt_listen` (code in `sensors/mqtt.py`, parsing in `sensors/ingest.py`).
- Default topics: `dough-monitor/#` (our probe script) and `rtl_433/+/events` (rtl_433 JSON events). Payload formats are documented in `sensors/ingest.py`.
- Readings from sensors not registered in the admin are ignored (rtl_433 hears the neighbours' devices too) and logged once.
- The listener uses a fixed client ID with a persistent session and QoS 1, so the broker queues readings while it is offline.

## Hardware
- The MQTT broker (Mosquitto) runs on a Raspberry Pi.
- Pi sensors: an RTL-SDR USB receiver picking up a 433 MHz temperature/humidity sensor (via rtl_433), plus a wired waterproof DS18B20 probe.
- `pi/` holds everything that runs on the Pi: the DS18B20 publisher script, the rtl_433 config and their systemd units.
- Django runs on a laptop / Linux desktop, possibly a GCP micro-server later (which would need broker TLS/auth or a VPN).

# Testing

All code will be tested. Use Django's testing framework (`django.test`), not pytest.

## Running the Suite

```
uv run python manage.py test --parallel
```

- The suite must be run before pushing to Github
- The suite will be run locally and in parallel.
- The database user needs `CREATEDB` so the parallel runner can create its test databases.
