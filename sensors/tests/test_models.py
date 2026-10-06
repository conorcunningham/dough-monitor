from datetime import UTC, datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import TestCase

from sensors.models import Reading, Sensor


class SensorTests(TestCase):
    def test_hardware_id_is_unique(self):
        Sensor.objects.create(hardware_id='28-0123456789ab', name='Probe', kind=Sensor.Kind.DOUGH)

        with self.assertRaises(IntegrityError):
            Sensor.objects.create(hardware_id='28-0123456789ab', name='Other', kind=Sensor.Kind.DOUGH)

    def test_changing_hardware_id_keeps_readings(self):
        sensor = Sensor.objects.create(hardware_id='Nexus-TH/42', name='Kitchen', kind=Sensor.Kind.AMBIENT)
        Reading.objects.create(sensor=sensor, recorded_at=datetime(2026, 10, 6, tzinfo=UTC), temperature_c=21.5)

        sensor.hardware_id = 'Nexus-TH/117'
        sensor.save()

        self.assertEqual(Sensor.objects.get(hardware_id='Nexus-TH/117').readings.count(), 1)


class ReadingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sensor = Sensor.objects.create(hardware_id='28-0123456789ab', name='Probe', kind=Sensor.Kind.DOUGH)
        cls.recorded_at = datetime(2026, 10, 6, 8, 0, tzinfo=UTC)

    def test_duplicate_reading_for_same_sensor_and_time_is_rejected(self):
        Reading.objects.create(sensor=self.sensor, recorded_at=self.recorded_at, temperature_c=24.0)

        with self.assertRaises(IntegrityError):
            Reading.objects.create(sensor=self.sensor, recorded_at=self.recorded_at, temperature_c=24.0)

    def test_humidity_is_optional(self):
        reading = Reading.objects.create(sensor=self.sensor, recorded_at=self.recorded_at, temperature_c=24.0)

        self.assertIsNone(reading.humidity)

    def test_humidity_above_100_fails_validation(self):
        reading = Reading(sensor=self.sensor, recorded_at=self.recorded_at, temperature_c=24.0, humidity=101)

        with self.assertRaises(ValidationError):
            reading.full_clean()

    def test_humidity_above_100_is_rejected_by_database(self):
        with self.assertRaises(IntegrityError):
            Reading.objects.create(sensor=self.sensor, recorded_at=self.recorded_at, temperature_c=24.0, humidity=101)

    def test_sensor_with_readings_cannot_be_deleted(self):
        Reading.objects.create(sensor=self.sensor, recorded_at=self.recorded_at, temperature_c=24.0)

        with self.assertRaises(ProtectedError):
            self.sensor.delete()
