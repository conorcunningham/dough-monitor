from datetime import timedelta
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from doughs.templatetags.dough_tags import duration, is_stale


class DurationFilterTests(SimpleTestCase):
    def test_formats(self):
        cases = [
            (None, ''),
            (timedelta(minutes=45), '45m'),
            (timedelta(hours=10, minutes=5), '10h 05m'),
            (timedelta(hours=1, seconds=59), '1h 00m'),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(duration(value), expected)


class IsStaleFilterTests(SimpleTestCase):
    def reading(self, minutes_ago):
        return SimpleNamespace(recorded_at=timezone.now() - timedelta(minutes=minutes_ago))

    def test_recent_reading_is_fresh(self):
        self.assertFalse(is_stale(self.reading(9)))

    def test_old_reading_is_stale(self):
        self.assertTrue(is_stale(self.reading(11)))

    def test_no_reading_is_not_stale(self):
        self.assertFalse(is_stale(None))
