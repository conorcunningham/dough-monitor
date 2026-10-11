from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from doughs.models import Dough, Fold, stray_readings
from sensors.models import Reading, Sensor

START = datetime(2026, 10, 6, 8, 0, tzinfo=UTC)


class DoughCreationTests(TestCase):
    def test_can_be_created_with_no_fields(self):
        before = timezone.now()

        dough = Dough.objects.create()

        self.assertGreaterEqual(dough.started_at, before)
        self.assertTrue(dough.is_collecting)

    def test_bake_defaults(self):
        dough = Dough.objects.create()

        self.assertEqual(dough.baking_temp_c, 250)
        self.assertEqual(dough.lid_on_minutes, 30)
        self.assertEqual(dough.lid_off_minutes, 20)

    def test_rating_outside_1_to_5_fails_validation(self):
        for rating in (0, 6):
            with self.subTest(rating=rating), self.assertRaises(ValidationError):
                Dough(rating=rating).full_clean()

    def test_rating_outside_1_to_5_is_rejected_by_database(self):
        with self.assertRaises(IntegrityError):
            Dough.objects.create(rating=6)


class DerivedValueTests(TestCase):
    def test_yeast_displayed_in_grams(self):
        self.assertEqual(Dough(yeast_mg=1500).yeast_g, Decimal('1.5'))

    def test_hydration_percent(self):
        self.assertEqual(Dough(flour_g=500, water_g=375).hydration_percent, Decimal('75'))

    def test_salt_percent(self):
        self.assertEqual(Dough(flour_g=500, salt_g=Decimal('10.0')).salt_percent, Decimal('2'))

    def test_derived_values_are_none_when_inputs_missing(self):
        dough = Dough(flour_g=0)

        self.assertIsNone(dough.yeast_g)
        self.assertIsNone(dough.hydration_percent)
        self.assertIsNone(dough.salt_percent)
        self.assertIsNone(dough.fermentation_duration)
        self.assertIsNone(dough.proofing_duration)

    def test_durations(self):
        dough = Dough(
            fermentation_started_at=START,
            fermentation_ended_at=START + timedelta(hours=10),
            proofing_started_at=START + timedelta(hours=11),
            proofing_ended_at=START + timedelta(hours=11, minutes=45),
        )

        self.assertEqual(dough.fermentation_duration, timedelta(hours=10))
        self.assertEqual(dough.proofing_duration, timedelta(minutes=45))


class CollectionWindowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.probe = Sensor.objects.create(hardware_id='28-0123456789ab', name='Probe', kind=Sensor.Kind.DOUGH)
        cls.other_probe = Sensor.objects.create(hardware_id='28-ba9876543210', name='Probe 2', kind=Sensor.Kind.DOUGH)
        cls.kitchen = Sensor.objects.create(hardware_id='Nexus-TH/42', name='Kitchen', kind=Sensor.Kind.AMBIENT)

    def reading(self, sensor, minutes):
        return Reading.objects.create(
            sensor=sensor,
            recorded_at=START + timedelta(minutes=minutes),
            temperature_c=24.0,
        )

    def test_new_dough_closes_open_dough_on_same_sensor(self):
        first = Dough.objects.create(dough_sensor=self.probe, started_at=START)
        second = Dough.objects.create(dough_sensor=self.probe, started_at=START + timedelta(days=1))

        first.refresh_from_db()
        self.assertEqual(first.collection_ended_at, second.started_at)
        self.assertTrue(second.is_collecting)

    def test_new_dough_leaves_doughs_on_other_sensors_open(self):
        first = Dough.objects.create(dough_sensor=self.probe, started_at=START)
        Dough.objects.create(dough_sensor=self.other_probe, started_at=START + timedelta(hours=1))

        first.refresh_from_db()
        self.assertTrue(first.is_collecting)

    def test_database_allows_only_one_collecting_dough_per_sensor(self):
        Dough.objects.create(dough_sensor=self.probe, started_at=START)

        with self.assertRaises(IntegrityError):
            # bulk_create bypasses save(), so nothing closes the first dough.
            Dough.objects.bulk_create([Dough(dough_sensor=self.probe, started_at=START)])

    def test_dough_readings_are_its_sensors_readings_since_start(self):
        dough = Dough.objects.create(dough_sensor=self.probe, ambient_sensor=self.kitchen, started_at=START)
        self.reading(self.probe, minutes=-1)
        inside = self.reading(self.probe, minutes=0)
        self.reading(self.other_probe, minutes=5)
        ambient = self.reading(self.kitchen, minutes=5)

        self.assertQuerySetEqual(dough.dough_readings(), [inside])
        self.assertQuerySetEqual(dough.ambient_readings(), [ambient])

    def test_readings_stop_at_collection_end(self):
        dough = Dough.objects.create(
            dough_sensor=self.probe,
            started_at=START,
            collection_ended_at=START + timedelta(hours=1),
        )
        inside = self.reading(self.probe, minutes=59)
        self.reading(self.probe, minutes=60)

        self.assertQuerySetEqual(dough.dough_readings(), [inside])

    def test_ambient_sensor_can_be_shared(self):
        first = Dough.objects.create(dough_sensor=self.probe, ambient_sensor=self.kitchen, started_at=START)
        second = Dough.objects.create(dough_sensor=self.other_probe, ambient_sensor=self.kitchen, started_at=START)
        reading = self.reading(self.kitchen, minutes=5)

        self.assertQuerySetEqual(first.ambient_readings(), [reading])
        self.assertQuerySetEqual(second.ambient_readings(), [reading])

    def test_no_sensor_means_no_readings(self):
        self.reading(self.probe, minutes=5)

        self.assertQuerySetEqual(Dough.objects.create(started_at=START).dough_readings(), [])

    def test_collection_cannot_end_before_start(self):
        with self.assertRaises(IntegrityError):
            Dough.objects.create(started_at=START, collection_ended_at=START - timedelta(minutes=1))

    def test_collecting_from_matches_dough_and_ambient_sensors_within_window(self):
        open_dough = Dough.objects.create(dough_sensor=self.probe, ambient_sensor=self.kitchen, started_at=START)
        closed = Dough.objects.create(
            dough_sensor=self.other_probe,
            started_at=START,
            collection_ended_at=START + timedelta(hours=1),
        )
        at = START + timedelta(minutes=30)

        self.assertQuerySetEqual(Dough.objects.collecting_from(self.probe, at=at), [open_dough])
        self.assertQuerySetEqual(Dough.objects.collecting_from(self.kitchen, at=at), [open_dough])
        self.assertQuerySetEqual(Dough.objects.collecting_from(self.other_probe, at=at), [closed])
        self.assertFalse(Dough.objects.collecting_from(self.other_probe, at=closed.collection_ended_at).exists())
        self.assertFalse(Dough.objects.collecting_from(self.probe, at=START - timedelta(seconds=1)).exists())

    def test_stray_readings_are_those_outside_every_window(self):
        Dough.objects.create(
            dough_sensor=self.probe,
            ambient_sensor=self.kitchen,
            started_at=START,
            collection_ended_at=START + timedelta(hours=1),
        )
        Dough.objects.create(dough_sensor=self.other_probe, started_at=START + timedelta(hours=2))
        before = self.reading(self.probe, minutes=-1)
        self.reading(self.probe, minutes=0)
        self.reading(self.kitchen, minutes=59)
        at_end = self.reading(self.probe, minutes=60)
        ambient_late = self.reading(self.kitchen, minutes=90)
        other_early = self.reading(self.other_probe, minutes=90)
        self.reading(self.other_probe, minutes=120)
        self.reading(self.other_probe, minutes=10_000)

        self.assertQuerySetEqual(stray_readings(), [before, at_end, ambient_late, other_early], ordered=False)

    def test_shared_ambient_sensor_is_covered_by_any_dough(self):
        Dough.objects.create(dough_sensor=self.probe, ambient_sensor=self.kitchen, started_at=START, collection_ended_at=START + timedelta(hours=1))
        Dough.objects.create(dough_sensor=self.other_probe, ambient_sensor=self.kitchen, started_at=START + timedelta(hours=1))
        self.reading(self.kitchen, minutes=30)
        self.reading(self.kitchen, minutes=90)

        self.assertFalse(stray_readings().exists())


class ButtonActionTests(TestCase):
    def setUp(self):
        self.dough = Dough.objects.create()

    def test_start_and_stop_fermentation(self):
        self.dough.start_fermentation()
        self.dough.stop_fermentation()

        self.dough.refresh_from_db()
        self.assertIsNotNone(self.dough.fermentation_started_at)
        self.assertGreaterEqual(self.dough.fermentation_ended_at, self.dough.fermentation_started_at)

    def test_start_and_stop_proofing(self):
        self.dough.start_proofing()
        self.dough.stop_proofing()

        self.dough.refresh_from_db()
        self.assertIsNotNone(self.dough.proofing_started_at)
        self.assertGreaterEqual(self.dough.proofing_ended_at, self.dough.proofing_started_at)

    def test_cannot_stop_before_starting(self):
        for stop in (self.dough.stop_fermentation, self.dough.stop_proofing):
            with self.subTest(stop=stop.__name__), self.assertRaisesMessage(ValidationError, 'has not started'):
                stop()

    def test_cannot_start_twice(self):
        for start in (self.dough.start_fermentation, self.dough.start_proofing):
            start()
            with self.subTest(start=start.__name__), self.assertRaisesMessage(ValidationError, 'already started'):
                start()

    def test_cannot_stop_twice(self):
        self.dough.start_fermentation()
        self.dough.stop_fermentation()

        with self.assertRaisesMessage(ValidationError, 'already stopped'):
            self.dough.stop_fermentation()

    def test_start_shaping_stops_collection(self):
        self.dough.start_shaping()

        self.dough.refresh_from_db()
        self.assertFalse(self.dough.is_collecting)

    def test_start_shaping_twice_is_rejected(self):
        self.dough.start_shaping()

        with self.assertRaisesMessage(ValidationError, 'already stopped'):
            self.dough.start_shaping()

    def test_fermentation_end_without_start_is_rejected_by_database(self):
        with self.assertRaises(IntegrityError):
            Dough.objects.create(fermentation_ended_at=START)


class FoldTests(TestCase):
    def test_folds_are_ordered_by_time(self):
        dough = Dough.objects.create()
        later = Fold.objects.create(dough=dough, folded_at=START + timedelta(minutes=30))
        earlier = Fold.objects.create(dough=dough, folded_at=START)

        self.assertQuerySetEqual(dough.folds.all(), [earlier, later])

    def test_folds_are_deleted_with_dough(self):
        dough = Dough.objects.create()
        Fold.objects.create(dough=dough)

        dough.delete()

        self.assertFalse(Fold.objects.exists())


class StartNewTests(TestCase):
    def test_uses_the_only_sensor_of_each_kind(self):
        probe = Sensor.objects.create(hardware_id='28-0123456789ab', name='Probe', kind=Sensor.Kind.DOUGH)
        kitchen = Sensor.objects.create(hardware_id='Nexus-TH/42', name='Kitchen', kind=Sensor.Kind.AMBIENT)

        dough = Dough.start_new()

        self.assertEqual(dough.dough_sensor, probe)
        self.assertEqual(dough.ambient_sensor, kitchen)

    def test_leaves_sensor_empty_when_choice_is_ambiguous(self):
        Sensor.objects.create(hardware_id='28-0123456789ab', name='Probe', kind=Sensor.Kind.DOUGH)
        Sensor.objects.create(hardware_id='28-ba9876543210', name='Probe 2', kind=Sensor.Kind.DOUGH)

        dough = Dough.start_new()

        self.assertIsNone(dough.dough_sensor)
        self.assertIsNone(dough.ambient_sensor)
