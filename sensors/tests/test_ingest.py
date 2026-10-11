import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from doughs.models import Dough
from sensors.ingest import ParsedReading, UnrecognisedMessage, UnregisteredSensor, parse_payload, store_reading
from sensors.models import Reading, Sensor


def payload(**data):
    return json.dumps(data).encode()


class ProbePayloadTests(SimpleTestCase):
    def test_parses_probe_payload(self):
        parsed = parse_payload(payload(
            sensor_id='28-0123456789ab',
            temperature_c=24.312,
            ts='2026-10-06T08:00:00+00:00',
        ))

        self.assertEqual(parsed, ParsedReading(
            hardware_id='28-0123456789ab',
            recorded_at=datetime(2026, 10, 6, 8, 0, tzinfo=UTC),
            temperature_c=24.312,
        ))

    def test_missing_timestamp_means_now(self):
        before = timezone.now()

        parsed = parse_payload(payload(sensor_id='28-0123456789ab', temperature_c=24.0))

        self.assertGreaterEqual(parsed.recorded_at, before)

    def test_epoch_timestamp(self):
        parsed = parse_payload(payload(sensor_id='28-0123456789ab', temperature_c=24.0, ts=1791273600))

        self.assertEqual(parsed.recorded_at, datetime.fromtimestamp(1791273600, tz=UTC))

    def test_missing_temperature_is_unrecognised(self):
        with self.assertRaises(UnrecognisedMessage):
            parse_payload(payload(sensor_id='28-0123456789ab'))


class Rtl433PayloadTests(SimpleTestCase):
    def test_parses_rtl_433_event(self):
        parsed = parse_payload(payload(
            time='2026-10-06 10:00:00+02:00',
            model='Nexus-TH',
            id=42,
            channel=1,
            battery_ok=1,
            temperature_C=21.3,
            humidity=55,
        ))

        self.assertEqual(parsed, ParsedReading(
            hardware_id='Nexus-TH/1/42',
            recorded_at=datetime(2026, 10, 6, 8, 0, tzinfo=UTC),
            temperature_c=21.3,
            humidity=55.0,
        ))

    def test_time_formats_rtl_433_can_emit(self):
        # pi/rtl_433.conf sets ``report_meta time:iso:tz``; the rest are what
        # ``time:unix`` (with and without ``usec``) produces.
        expected = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
        cases = {
            'iso with offset': '2026-10-09T12:00:00+0200',
            'iso utc': '2026-10-09T10:00:00Z',
            'unix': str(int(expected.timestamp())),
            'unix with usec': f'{expected.timestamp():.6f}',
        }
        for name, time in cases.items():
            with self.subTest(name):
                parsed = parse_payload(payload(model='Nexus-TH', id=42, temperature_C=21.0, time=time))
                self.assertEqual(parsed.recorded_at, expected)

    def test_extra_fields_and_lettered_channel(self):
        # ``report_meta level`` adds signal fields; some sensors use lettered channels.
        parsed = parse_payload(payload(
            time='2026-10-09T12:00:00+0200',
            model='Acurite-Tower',
            id=1234,
            channel='A',
            battery_ok=1,
            temperature_C=21.3,
            humidity=55,
            mic='CHECKSUM',
            mod='ASK',
            freq=433.918,
            rssi=-0.1,
            snr=20.5,
            noise=-20.6,
        ))

        self.assertEqual(parsed.hardware_id, 'Acurite-Tower/A/1234')
        self.assertEqual(parsed.temperature_c, 21.3)
        self.assertEqual(parsed.humidity, 55.0)

    def test_hardware_id_without_channel(self):
        parsed = parse_payload(payload(model='Acurite-Tower', id=7, temperature_C=20.0))

        self.assertEqual(parsed.hardware_id, 'Acurite-Tower/7')

    def test_fahrenheit_is_converted(self):
        parsed = parse_payload(payload(model='LaCrosse-TX', id=1, temperature_F=212.0))

        self.assertAlmostEqual(parsed.temperature_c, 100.0)

    @override_settings(TIME_ZONE='Europe/Oslo')
    def test_naive_time_is_treated_as_local(self):
        parsed = parse_payload(payload(model='Nexus-TH', id=42, temperature_C=21.0, time='2026-10-06 10:00:00'))

        self.assertEqual(parsed.recorded_at, datetime(2026, 10, 6, 10, 0, tzinfo=ZoneInfo('Europe/Oslo')))

    def test_out_of_range_humidity_is_dropped(self):
        parsed = parse_payload(payload(model='Nexus-TH', id=42, temperature_C=21.0, humidity=120))

        self.assertEqual(parsed.temperature_c, 21.0)
        self.assertIsNone(parsed.humidity)

    def test_event_without_temperature_is_unrecognised(self):
        with self.assertRaisesMessage(UnrecognisedMessage, 'no temperature'):
            parse_payload(payload(model='Doorbell', id=3, button=1))


class UnrecognisedPayloadTests(SimpleTestCase):
    def test_rejects(self):
        cases = {
            'not json': b'21.5',
            'not utf-8': b'\xff\xfe',
            'json list': b'[1, 2]',
            'unknown shape': payload(hello='world'),
            'bad temperature': payload(sensor_id='28-x', temperature_c='warm'),
            'bad time': payload(sensor_id='28-x', temperature_c=20, ts='yesterday'),
            'null temperature': payload(sensor_id='28-x', temperature_c=None),
            'list temperature': payload(sensor_id='28-x', temperature_c=[21.0]),
            'boolean temperature': payload(sensor_id='28-x', temperature_c=True),
            'nan temperature': b'{"sensor_id": "28-x", "temperature_c": NaN}',
            'infinite temperature': b'{"sensor_id": "28-x", "temperature_c": Infinity}',
            'rtl_433 bad temperature': payload(model='Nexus-TH', id=1, temperature_C='21,5'),
            'rtl_433 bad fahrenheit': payload(model='LaCrosse-TX', id=1, temperature_F=None),
        }
        for name, raw in cases.items():
            with self.subTest(name), self.assertRaises(UnrecognisedMessage):
                parse_payload(raw)


class TemperatureValidationTests(SimpleTestCase):
    def test_accepts_numbers_and_numeric_strings(self):
        cases = {'int': 21, 'float': 21.5, 'string': '21.5', 'negative': -20.25}
        for name, value in cases.items():
            with self.subTest(name):
                parsed = parse_payload(payload(sensor_id='28-x', temperature_c=value))
                self.assertEqual(parsed.temperature_c, float(value))

    def test_accepts_range_limits(self):
        for value in (-55, 125):
            with self.subTest(value):
                parsed = parse_payload(payload(model='Nexus-TH', id=1, temperature_C=value))
                self.assertEqual(parsed.temperature_c, value)

    def test_rejects_out_of_range_celsius(self):
        for value in (-55.1, 125.1, 850.0, -3276.8):
            with self.subTest(value), self.assertRaisesMessage(UnrecognisedMessage, 'outside'):
                parse_payload(payload(sensor_id='28-x', temperature_c=value))

    def test_rejects_out_of_range_after_fahrenheit_conversion(self):
        with self.assertRaisesMessage(UnrecognisedMessage, 'outside'):
            parse_payload(payload(model='LaCrosse-TX', id=1, temperature_F=300.0))

    def test_non_finite_humidity_is_dropped_not_fatal(self):
        cases = {
            'nan': b'{"model": "Nexus-TH", "id": 1, "temperature_C": 21.0, "humidity": NaN}',
            'text': payload(model='Nexus-TH', id=1, temperature_C=21.0, humidity='damp'),
            'boolean': payload(model='Nexus-TH', id=1, temperature_C=21.0, humidity=True),
        }
        for name, raw in cases.items():
            with self.subTest(name):
                parsed = parse_payload(raw)
                self.assertEqual(parsed.temperature_c, 21.0)
                self.assertIsNone(parsed.humidity)


class StoreReadingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.kitchen = Sensor.objects.create(hardware_id='Nexus-TH/1/42', name='Kitchen', kind=Sensor.Kind.AMBIENT)
        cls.probe = Sensor.objects.create(hardware_id='28-0123456789ab', name='Probe', kind=Sensor.Kind.DOUGH)
        cls.parsed = ParsedReading(
            hardware_id='Nexus-TH/1/42',
            recorded_at=datetime(2026, 10, 6, 8, 0, tzinfo=UTC),
            temperature_c=21.3,
            humidity=55.0,
        )
        cls.dough = Dough.objects.create(
            dough_sensor=cls.probe,
            ambient_sensor=cls.kitchen,
            started_at=cls.parsed.recorded_at - timedelta(hours=1),
        )

    def test_stores_reading_for_registered_sensor(self):
        reading = store_reading(self.parsed)

        self.assertEqual(reading.sensor, self.kitchen)
        self.assertEqual(reading.temperature_c, 21.3)
        self.assertEqual(reading.humidity, 55.0)

    def test_unregistered_sensor_is_rejected(self):
        with self.assertRaises(UnregisteredSensor):
            store_reading(ParsedReading('Nexus-TH/2/99', timezone.now(), 18.0))

        self.assertFalse(Reading.objects.exists())

    def test_stores_reading_for_dough_sensor(self):
        reading = store_reading(replace(self.parsed, hardware_id=self.probe.hardware_id, humidity=None))

        self.assertEqual(reading.sensor, self.probe)

    def test_dropped_when_no_dough_is_collecting_from_sensor(self):
        self.dough.dough_sensor = None
        self.dough.save()
        self.assertIsNone(store_reading(replace(self.parsed, hardware_id=self.probe.hardware_id)))

        self.dough.collection_ended_at = self.parsed.recorded_at
        self.dough.save()
        self.assertIsNone(store_reading(self.parsed))
        self.assertFalse(Reading.objects.exists())

    def test_dropped_when_recorded_before_dough_started(self):
        # The broker queues readings while we are offline, so the reading's own time counts, not now.
        reading = store_reading(self._at(hours=-2))

        self.assertIsNone(reading)
        self.assertFalse(Reading.objects.exists())

    def test_queued_reading_inside_a_closed_window_is_stored(self):
        self.dough.collection_ended_at = self.parsed.recorded_at + timedelta(minutes=1)
        self.dough.save()

        self.assertIsNotNone(store_reading(self.parsed))

    def test_stored_when_only_an_ambient_dough_uses_the_sensor(self):
        Dough.objects.create(dough_sensor=self.probe, ambient_sensor=self.kitchen, started_at=self.parsed.recorded_at)

        self.assertIsNotNone(store_reading(self.parsed))

    def test_redelivered_reading_is_stored_once(self):
        first = store_reading(self.parsed)
        second = store_reading(self.parsed)

        self.assertEqual(first, second)
        self.assertEqual(Reading.objects.count(), 1)

    def test_repeats_of_one_transmission_are_stored_once(self):
        # A WT450-TH sends each reading three times within about a second,
        # which can straddle a second boundary. Later readings are new data.
        first = store_reading(self.parsed)
        repeat = store_reading(self._at(seconds=1))
        later = store_reading(self._at(seconds=30))

        self.assertEqual(repeat, first)
        self.assertNotEqual(later, first)
        self.assertEqual(Reading.objects.count(), 2)

    def test_repeat_window_does_not_look_forward(self):
        store_reading(self._at(seconds=1))

        self.assertEqual(Reading.objects.count(), 1)
        store_reading(self.parsed)
        self.assertEqual(Reading.objects.count(), 2)

    def _at(self, **delta):
        return replace(self.parsed, recorded_at=self.parsed.recorded_at + timedelta(**delta))
