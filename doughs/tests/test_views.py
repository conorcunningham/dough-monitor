from datetime import UTC, datetime, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from doughs.models import Dough, Fold
from sensors.models import Reading, Sensor

HTMX = {'HTTP_HX_REQUEST': 'true'}


class HomeTests(TestCase):
    def test_redirects_to_list_when_there_are_no_doughs(self):
        response = self.client.get(reverse('doughs:home'))

        self.assertRedirects(response, reverse('doughs:list'))

    def test_redirects_to_most_recent_dough(self):
        Dough.objects.create(started_at=timezone.now() - timedelta(days=1))
        latest = Dough.objects.create()

        response = self.client.get(reverse('doughs:home'))

        self.assertRedirects(response, reverse('doughs:detail', args=[latest.pk]))


class ListTests(TestCase):
    def test_lists_doughs_with_start_button(self):
        dough = Dough.objects.create(flour_g=500, water_g=350)

        response = self.client.get(reverse('doughs:list'))

        self.assertContains(response, 'Start new dough')
        self.assertContains(response, reverse('doughs:detail', args=[dough.pk]))
        self.assertContains(response, '70.0%')


class StartTests(TestCase):
    def test_post_starts_dough_and_redirects_to_it(self):
        response = self.client.post(reverse('doughs:start'))

        dough = Dough.objects.get()
        self.assertRedirects(response, reverse('doughs:detail', args=[dough.pk]))

    def test_get_is_not_allowed(self):
        response = self.client.get(reverse('doughs:start'))

        self.assertEqual(response.status_code, 405)
        self.assertFalse(Dough.objects.exists())


class DetailTests(TestCase):
    def setUp(self):
        self.dough = Dough.objects.create()
        self.url = reverse('doughs:detail', args=[self.dough.pk])

    def form_data(self, **overrides):
        data = {'baking_temp_c': 250, 'lid_on_minutes': 30, 'lid_off_minutes': 20}
        data.update(overrides)
        return data

    def test_renders_page_with_panels(self):
        response = self.client.get(self.url)

        self.assertTemplateUsed(response, 'doughs/detail.html')
        self.assertContains(response, 'id="latest"')
        self.assertContains(response, 'id="timers"')
        self.assertContains(response, 'id="dough-form"')
        self.assertContains(response, 'id="readings-chart"')

    def test_missing_dough_is_404(self):
        response = self.client.get(reverse('doughs:detail', args=[999]))

        self.assertEqual(response.status_code, 404)

    def test_post_saves_and_redirects(self):
        response = self.client.post(self.url, self.form_data(flour_g=500, water_g=375, rating=4, rise='full'))

        self.assertRedirects(response, self.url)
        self.dough.refresh_from_db()
        self.assertEqual(self.dough.flour_g, 500)
        self.assertEqual(self.dough.rating, 4)
        self.assertEqual(self.dough.rise, Dough.Rise.FULL)

    def test_yeast_is_entered_in_grams_and_stored_in_milligrams(self):
        self.client.post(self.url, self.form_data(yeast_g='1.5'))

        self.dough.refresh_from_db()
        self.assertEqual(self.dough.yeast_mg, 1500)

    def test_yeast_is_shown_in_grams(self):
        self.dough.yeast_mg = 1500
        self.dough.save()

        response = self.client.get(self.url)

        self.assertContains(response, 'name="yeast_g" value="1.5"')

    def test_clearing_yeast_stores_none(self):
        self.dough.yeast_mg = 1500
        self.dough.save()

        self.client.post(self.url, self.form_data(yeast_g=''))

        self.dough.refresh_from_db()
        self.assertIsNone(self.dough.yeast_mg)

    def test_htmx_post_returns_form_partial_with_saved_message(self):
        response = self.client.post(self.url, self.form_data(flour_g=500, water_g=375), **HTMX)

        self.assertTemplateUsed(response, 'doughs/partials/form.html')
        self.assertTemplateNotUsed(response, 'base.html')
        self.assertContains(response, 'Saved.')
        self.assertContains(response, 'Hydration 75.0%')

    def test_invalid_post_shows_errors_and_does_not_save(self):
        response = self.client.post(self.url, self.form_data(rating=6), **HTMX)

        self.assertNotContains(response, 'Saved.')
        self.assertTrue(response.context['form'].errors['rating'])
        self.dough.refresh_from_db()
        self.assertIsNone(self.dough.rating)

    def test_sensor_choices_are_limited_by_kind(self):
        probe = Sensor.objects.create(hardware_id='28-0123456789ab', name='Probe', kind=Sensor.Kind.DOUGH)
        kitchen = Sensor.objects.create(hardware_id='Nexus-TH/42', name='Kitchen', kind=Sensor.Kind.AMBIENT)

        form = self.client.get(self.url).context['form']

        self.assertQuerySetEqual(form.fields['dough_sensor'].queryset, [probe])
        self.assertQuerySetEqual(form.fields['ambient_sensor'].queryset, [kitchen])


class TimerActionTests(TestCase):
    def setUp(self):
        self.dough = Dough.objects.create()

    def post(self, action, **extra):
        return self.client.post(reverse('doughs:timer', args=[self.dough.pk, action]), **extra)

    def test_each_action_sets_its_timestamp(self):
        steps = [
            ('start-fermentation', 'fermentation_started_at'),
            ('stop-fermentation', 'fermentation_ended_at'),
            ('start-proofing', 'proofing_started_at'),
            ('stop-proofing', 'proofing_ended_at'),
            ('start-shaping', 'collection_ended_at'),
        ]
        for action, field in steps:
            with self.subTest(action=action):
                self.post(action, **HTMX)

                self.dough.refresh_from_db()
                self.assertIsNotNone(getattr(self.dough, field))

    def test_htmx_returns_timers_partial(self):
        response = self.post('start-fermentation', **HTMX)

        self.assertTemplateUsed(response, 'doughs/partials/timers.html')
        self.assertTemplateNotUsed(response, 'base.html')
        self.assertContains(response, 'Stop Fermentation')

    def test_plain_post_redirects_to_detail(self):
        response = self.post('start-fermentation')

        self.assertRedirects(response, reverse('doughs:detail', args=[self.dough.pk]))

    def test_invalid_action_shows_error_in_partial(self):
        response = self.post('stop-fermentation', **HTMX)

        self.assertContains(response, 'Fermentation has not started.')

    def test_invalid_action_without_htmx_shows_error_after_redirect(self):
        response = self.post('stop-fermentation', follow=True)

        self.assertContains(response, 'Fermentation has not started.')

    def test_unknown_action_is_404(self):
        response = self.post('start-baking', **HTMX)

        self.assertEqual(response.status_code, 404)

    def test_get_is_not_allowed(self):
        response = self.client.get(reverse('doughs:timer', args=[self.dough.pk, 'start-fermentation']))

        self.assertEqual(response.status_code, 405)

    def test_start_shaping_asks_for_confirmation(self):
        response = self.client.get(reverse('doughs:detail', args=[self.dough.pk]))

        self.assertContains(response, 'hx-confirm="Stop collecting readings for this dough?"')


class FoldViewTests(TestCase):
    def setUp(self):
        self.dough = Dough.objects.create()

    def test_add_fold(self):
        response = self.client.post(reverse('doughs:fold_add', args=[self.dough.pk]), **HTMX)

        fold = self.dough.folds.get()
        self.assertContains(response, timezone.localtime(fold.folded_at).strftime('%H:%M'))

    def test_delete_fold(self):
        fold = Fold.objects.create(dough=self.dough)

        self.client.post(reverse('doughs:fold_delete', args=[self.dough.pk, fold.pk]), **HTMX)

        self.assertFalse(self.dough.folds.exists())

    def test_cannot_delete_another_doughs_fold(self):
        other = Fold.objects.create(dough=Dough.objects.create())

        response = self.client.post(reverse('doughs:fold_delete', args=[self.dough.pk, other.pk]), **HTMX)

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Fold.objects.filter(pk=other.pk).exists())


class ReadingsViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.probe = Sensor.objects.create(hardware_id='28-0123456789ab', name='Probe', kind=Sensor.Kind.DOUGH)
        cls.kitchen = Sensor.objects.create(hardware_id='Nexus-TH/42', name='Kitchen', kind=Sensor.Kind.AMBIENT)

    def setUp(self):
        self.start = timezone.now() - timedelta(hours=1)
        self.dough = Dough.objects.create(dough_sensor=self.probe, ambient_sensor=self.kitchen, started_at=self.start)

    def reading(self, sensor, recorded_at, temperature_c, humidity=None):
        return Reading.objects.create(
            sensor=sensor, recorded_at=recorded_at, temperature_c=temperature_c, humidity=humidity
        )

    def test_readings_json(self):
        recorded_at = datetime(2030, 1, 1, tzinfo=UTC)
        self.reading(self.probe, recorded_at, 24.5)
        self.reading(self.kitchen, recorded_at, 21.0, humidity=55.0)
        self.reading(self.kitchen, recorded_at + timedelta(minutes=1), 21.1)
        self.reading(self.probe, self.start - timedelta(minutes=1), 30.0)
        ms = int(recorded_at.timestamp() * 1000)

        response = self.client.get(reverse('doughs:readings', args=[self.dough.pk]))

        self.assertEqual(response.json(), {
            'dough': [{'x': ms, 'y': 24.5}],
            'ambient': [{'x': ms, 'y': 21.0}, {'x': ms + 60_000, 'y': 21.1}],
            'humidity': [{'x': ms, 'y': 55.0}],
        })

    def test_latest_shows_most_recent_readings(self):
        now = timezone.now()
        self.reading(self.probe, now - timedelta(minutes=2), 24.0)
        self.reading(self.probe, now - timedelta(minutes=1), 24.6)
        self.reading(self.kitchen, now - timedelta(minutes=1), 21.0, humidity=55.0)

        response = self.client.get(reverse('doughs:latest', args=[self.dough.pk]))

        self.assertContains(response, '24.6 °C')
        self.assertContains(response, '21.0 °C')
        self.assertContains(response, '55% RH')
        self.assertNotContains(response, 'stale')

    def test_latest_flags_stale_readings(self):
        self.reading(self.probe, timezone.now() - timedelta(minutes=11), 24.0)

        response = self.client.get(reverse('doughs:latest', args=[self.dough.pk]))

        self.assertContains(response, '<mark>stale</mark>')

    def test_latest_polls_only_while_collecting(self):
        url = reverse('doughs:latest', args=[self.dough.pk])
        self.assertContains(self.client.get(url), 'hx-trigger="every 30s"')

        self.dough.start_shaping()

        self.assertNotContains(self.client.get(url), 'hx-trigger')


class ReadingsOverviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.probe = Sensor.objects.create(hardware_id='28-0123456789ab', name='Probe', kind=Sensor.Kind.DOUGH)
        start = datetime(2026, 10, 6, 8, 0, tzinfo=UTC)
        Dough.objects.create(dough_sensor=cls.probe, started_at=start, collection_ended_at=start + timedelta(hours=1))
        cls.inside = Reading.objects.create(sensor=cls.probe, recorded_at=start, temperature_c=24.0)
        for minutes in (-10, 70):
            Reading.objects.create(sensor=cls.probe, recorded_at=start + timedelta(minutes=minutes), temperature_c=24.0)

    def test_page_shows_counts_and_delete_button(self):
        response = self.client.get(reverse('doughs:readings_overview'))

        self.assertContains(response, 'Probe')
        self.assertContains(response, '2 readings fall outside')
        self.assertContains(response, 'Delete stray readings')

    def test_page_without_strays_has_no_button(self):
        Reading.objects.exclude(pk=self.inside.pk).delete()

        response = self.client.get(reverse('doughs:readings_overview'))

        self.assertContains(response, 'No stray readings')
        self.assertNotContains(response, 'Delete stray readings')

    def test_prune_deletes_only_stray_readings(self):
        response = self.client.post(reverse('doughs:readings_prune'), follow=True)

        self.assertRedirects(response, reverse('doughs:readings_overview'))
        self.assertQuerySetEqual(Reading.objects.all(), [self.inside])
        self.assertContains(response, 'Deleted 2 readings')

    def test_prune_requires_post(self):
        response = self.client.get(reverse('doughs:readings_prune'))

        self.assertEqual(response.status_code, 405)
        self.assertEqual(Reading.objects.count(), 3)

    def test_nav_links_to_readings_page(self):
        response = self.client.get(reverse('doughs:list'))

        self.assertContains(response, reverse('doughs:readings_overview'))
