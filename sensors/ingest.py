"""Turn MQTT payloads into Readings.

Two payload shapes are understood:

* The DS18B20 probe script in ``pi/``::

    {"sensor_id": "28-0123456789ab", "temperature_c": 24.31, "ts": "2026-10-06T08:00:00+00:00"}

* rtl_433 JSON events (``rtl_433 -F mqtt``, topic ``rtl_433/<host>/events``)::

    {"time": "2026-10-09T12:00:00+0200", "model": "Nexus-TH", "id": 42, "channel": 1,
     "battery_ok": 1, "temperature_C": 21.3, "humidity": 55, "mic": "CHECKSUM"}

  The hardware ID is built as ``model/channel/id`` (channel omitted when absent).
  ``pi/rtl_433.conf`` asks for ISO times with a UTC offset; rtl_433's default is
  naive local time (``"2026-10-09 12:00:00"``), which is taken to be in TIME_ZONE.
  Other fields (battery, signal level, ...) are ignored.
"""

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from django.utils import timezone

from doughs.models import Dough

from .models import Reading, Sensor


class UnrecognisedMessage(ValueError):
    pass


class UnregisteredSensor(LookupError):
    pass


# Many 433 MHz sensors send each packet two or three times per transmission
# (a WT450-TH sends three within a second) and rtl_433 reports every copy.
# No sensor sends genuinely new readings this close together.
REPEAT_WINDOW = timedelta(seconds=5)

# The DS18B20's rated range. Nothing we measure (dough, kitchen) gets near either end,
# so anything outside it is a corrupted packet, a sensor fault or a bad conversion.
TEMPERATURE_RANGE_C = (-55.0, 125.0)


@dataclass(frozen=True)
class ParsedReading:
    hardware_id: str
    recorded_at: datetime
    temperature_c: float
    humidity: float | None = None


def parse_payload(payload):
    try:
        data = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise UnrecognisedMessage('Payload is not JSON.') from e
    if not isinstance(data, dict):
        raise UnrecognisedMessage('Payload is not a JSON object.')

    try:
        if 'sensor_id' in data:
            return _parse_probe(data)
        if 'model' in data:
            return _parse_rtl_433(data)
    except (KeyError, TypeError, ValueError) as e:
        raise UnrecognisedMessage(f'Malformed payload: {e}') from e
    raise UnrecognisedMessage('Payload is neither a probe reading nor an rtl_433 event.')


def store_reading(parsed):
    """Save a reading for a registered sensor that a dough is collecting from.

    Raises UnregisteredSensor for a sensor not in the admin. Returns None when no dough's
    collection window covers the reading's time, so idle sensors don't fill the database.
    A repeat of a transmission (or a QoS 1 redelivery) returns the reading already stored.
    """
    sensor = Sensor.objects.filter(hardware_id=parsed.hardware_id).first()
    if sensor is None:
        raise UnregisteredSensor(parsed.hardware_id)
    if not Dough.objects.collecting_from(sensor, at=parsed.recorded_at).exists():
        return None
    repeat_of = (
        Reading.objects.filter(
            sensor=sensor,
            recorded_at__gt=parsed.recorded_at - REPEAT_WINDOW,
            recorded_at__lte=parsed.recorded_at,
        )
        .order_by('-recorded_at')
        .first()
    )
    if repeat_of is not None:
        return repeat_of
    reading, _ = Reading.objects.get_or_create(
        sensor=sensor,
        recorded_at=parsed.recorded_at,
        defaults={'temperature_c': parsed.temperature_c, 'humidity': parsed.humidity},
    )
    return reading


def _parse_probe(data):
    return ParsedReading(
        hardware_id=str(data['sensor_id']),
        recorded_at=_parse_time(data.get('ts')),
        temperature_c=_parse_temperature(data['temperature_c']),
    )


def _parse_rtl_433(data):
    if 'temperature_C' in data:
        temperature_c = _parse_temperature(data['temperature_C'])
    elif 'temperature_F' in data:
        temperature_c = _parse_temperature((_parse_number(data['temperature_F']) - 32) * 5 / 9)
    else:
        raise UnrecognisedMessage(f'rtl_433 event from {data["model"]} has no temperature.')

    parts = [data['model'], data.get('channel'), data.get('id')]
    return ParsedReading(
        hardware_id='/'.join(str(part) for part in parts if part is not None),
        recorded_at=_parse_time(data.get('time')),
        temperature_c=temperature_c,
        humidity=_parse_humidity(data.get('humidity')),
    )


def _parse_number(value):
    """A finite float from a JSON number or numeric string. Booleans and NaN/inf are rejected."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ValueError(f'{value!r} is not a number.')
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f'{value!r} is not a finite number.')
    return number


def _parse_temperature(value):
    temperature_c = _parse_number(value)
    low, high = TEMPERATURE_RANGE_C
    if not low <= temperature_c <= high:
        raise ValueError(f'Temperature {temperature_c:g} C is outside {low:g}..{high:g} C.')
    return temperature_c


def _parse_humidity(value):
    # Cheap 433 MHz sensors occasionally send garbage; drop it rather than the whole reading.
    if value is None:
        return None
    try:
        humidity = _parse_number(value)
    except ValueError:
        return None
    return humidity if 0 <= humidity <= 100 else None


def _parse_time(value):
    """Accept ISO 8601 (with or without offset) or Unix epoch seconds. Missing means now."""
    if value is None:
        return timezone.now()
    if isinstance(value, int | float) or (isinstance(value, str) and value.replace('.', '', 1).isdigit()):
        return datetime.fromtimestamp(float(value), tz=UTC)
    recorded_at = datetime.fromisoformat(value)
    if timezone.is_naive(recorded_at):
        # rtl_433 sends local time by default; assume the Pi shares our time zone.
        recorded_at = timezone.make_aware(recorded_at)
    return recorded_at
