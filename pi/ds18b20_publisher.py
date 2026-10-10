#!/usr/bin/env python3
"""Publish DS18B20 probe temperatures to MQTT. Runs on the Raspberry Pi.

Setup on the Pi: ``sudo pi/setup.sh`` does all of this. By hand:
    1. Enable 1-Wire: add ``dtoverlay=w1-gpio`` to /boot/firmware/config.txt and reboot.
    2. Check the probe shows up: ``ls /sys/bus/w1/devices/`` should list ``28-...``.
    3. ``sudo apt install python3-paho-mqtt``.
    4. Run it (``python3 ds18b20_publisher.py``) to check, then install it to
       /usr/local/bin and enable pi/dough-probe.service (see the unit's header).

Each probe found is published every PROBE_INTERVAL seconds to
``<PROBE_TOPIC_PREFIX>/<sensor id>`` as:

    {"sensor_id": "28-0123456789ab", "temperature_c": 24.312, "ts": "2026-10-06T08:00:00+00:00"}

Configuration (environment variables):
    MQTT_HOST           default: localhost (the broker runs on this Pi)
    MQTT_PORT           default: 1883
    MQTT_USERNAME       optional
    MQTT_PASSWORD       optional
    PROBE_TOPIC_PREFIX  default: dough-monitor/probe
    PROBE_INTERVAL      default: 30
"""

import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

W1_DEVICES = Path('/sys/bus/w1/devices')

# 85 °C is the DS18B20's power-on reset value and -127 °C means the read failed.
# Neither is a real dough temperature.
INVALID_TEMPERATURES = {85.0, -127.0}

QOS = 1

logger = logging.getLogger('ds18b20_publisher')


def find_probes(devices=W1_DEVICES):
    return sorted(devices.glob('28-*'))


def read_temperature(probe):
    """Return the probe's temperature in °C, or None if the read was bad."""
    try:
        lines = (probe / 'w1_slave').read_text().splitlines()
    except OSError as e:
        logger.warning('Could not read %s: %s', probe.name, e)
        return None

    # Line 1 ends with the CRC check result; line 2 ends with "t=<millidegrees>".
    if len(lines) < 2 or not lines[0].strip().endswith('YES'):
        logger.warning('CRC check failed for %s', probe.name)
        return None
    _, found, millidegrees = lines[1].partition('t=')
    if not found:
        return None

    temperature_c = int(millidegrees) / 1000
    if temperature_c in INVALID_TEMPERATURES:
        logger.warning('Ignoring invalid reading %.1f °C from %s', temperature_c, probe.name)
        return None
    return temperature_c


def build_payload(sensor_id, temperature_c, recorded_at):
    return json.dumps({
        'sensor_id': sensor_id,
        'temperature_c': temperature_c,
        'ts': recorded_at.isoformat(),
    })


def publish_once(client, topic_prefix, devices=W1_DEVICES):
    """Read every probe and publish its temperature. Returns how many were published."""
    published = 0
    for probe in find_probes(devices):
        temperature_c = read_temperature(probe)
        if temperature_c is None:
            continue
        payload = build_payload(probe.name, temperature_c, datetime.now(UTC))
        client.publish(f'{topic_prefix}/{probe.name}', payload, qos=QOS)
        published += 1
    return published


def main():
    import paho.mqtt.client as mqtt

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    host = os.environ.get('MQTT_HOST', 'localhost')
    port = int(os.environ.get('MQTT_PORT', '1883'))
    topic_prefix = os.environ.get('PROBE_TOPIC_PREFIX', 'dough-monitor/probe')
    interval = float(os.environ.get('PROBE_INTERVAL', '30'))

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='dough-monitor-probe')
    if os.environ.get('MQTT_USERNAME'):
        client.username_pw_set(os.environ['MQTT_USERNAME'], os.environ.get('MQTT_PASSWORD'))
    client.connect_async(host, port)
    client.loop_start()
    logger.info('Publishing to %s:%s every %ss under %s/', host, port, interval, topic_prefix)

    if not find_probes():
        logger.warning(
            'No DS18B20 probes found in %s (1-Wire overlay loaded? probe wired?). Will keep looking.',
            W1_DEVICES,
        )

    try:
        while True:
            publish_once(client, topic_prefix)
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == '__main__':
    main()
