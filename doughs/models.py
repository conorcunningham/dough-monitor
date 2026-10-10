from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import F, Q
from django.utils import timezone

from sensors.models import Reading, Sensor


class Dough(models.Model):
    class Rise(models.TextChoices):
        LOW = 'low', 'Low'
        MEDIUM = 'medium', 'Medium'
        FULL = 'full', 'Full'
        EXCELLENT = 'excellent', 'Excellent'

    # Readings collection window. Collection stops at "Start Shaping", or
    # automatically when a new dough is started on the same dough sensor.
    started_at = models.DateTimeField(default=timezone.now)
    collection_ended_at = models.DateTimeField(null=True, blank=True)

    dough_sensor = models.ForeignKey(
        Sensor,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='doughs',
        limit_choices_to={'kind': Sensor.Kind.DOUGH},
    )
    # Several doughs may share one ambient sensor.
    ambient_sensor = models.ForeignKey(
        Sensor,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='ambient_doughs',
        limit_choices_to={'kind': Sensor.Kind.AMBIENT},
    )

    # Ingredients
    flour_g = models.PositiveIntegerField(null=True, blank=True)
    water_g = models.PositiveIntegerField(null=True, blank=True)
    water_temp_c = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    yeast_mg = models.PositiveIntegerField(null=True, blank=True)
    salt_g = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)

    # Recorded with start/stop buttons; durations are derived.
    fermentation_started_at = models.DateTimeField(null=True, blank=True)
    fermentation_ended_at = models.DateTimeField(null=True, blank=True)
    proofing_started_at = models.DateTimeField(null=True, blank=True)
    proofing_ended_at = models.DateTimeField(null=True, blank=True)

    # Pre-planned bake.
    baking_temp_c = models.PositiveSmallIntegerField(default=250)
    lid_on_minutes = models.PositiveSmallIntegerField(default=30)
    lid_off_minutes = models.PositiveSmallIntegerField(default=20)

    # Outcome
    notes = models.TextField(blank=True)
    rating = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    rise = models.CharField(max_length=10, choices=Rise.choices, blank=True)

    class Meta:
        ordering = ['-started_at']
        constraints = [
            models.UniqueConstraint(
                fields=['dough_sensor'],
                condition=Q(collection_ended_at__isnull=True),
                name='one_collecting_dough_per_sensor',
            ),
            models.CheckConstraint(
                condition=Q(rating__isnull=True) | Q(rating__gte=1, rating__lte=5),
                name='dough_rating_1_to_5',
            ),
            models.CheckConstraint(
                condition=Q(collection_ended_at__isnull=True) | Q(collection_ended_at__gte=F('started_at')),
                name='dough_collection_ends_after_start',
            ),
            models.CheckConstraint(
                condition=Q(fermentation_ended_at__isnull=True) | Q(
                    fermentation_started_at__isnull=False,
                    fermentation_ended_at__gte=F('fermentation_started_at'),
                ),
                name='dough_fermentation_ends_after_start',
            ),
            models.CheckConstraint(
                condition=Q(proofing_ended_at__isnull=True) | Q(
                    proofing_started_at__isnull=False,
                    proofing_ended_at__gte=F('proofing_started_at'),
                ),
                name='dough_proofing_ends_after_start',
            ),
        ]

    def __str__(self):
        return f'Dough started {timezone.localtime(self.started_at):%Y-%m-%d %H:%M}'

    def save(self, *args, **kwargs):
        if self._state.adding and self.dough_sensor_id:
            with transaction.atomic():
                Dough.objects.filter(
                    dough_sensor_id=self.dough_sensor_id,
                    collection_ended_at__isnull=True,
                ).update(collection_ended_at=self.started_at)
                super().save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)

    @classmethod
    def start_new(cls):
        """Start a dough now, using the only dough/ambient sensor when there is exactly one of each."""
        return cls.objects.create(
            dough_sensor=_only_sensor(Sensor.Kind.DOUGH),
            ambient_sensor=_only_sensor(Sensor.Kind.AMBIENT),
        )

    # Derived values

    @property
    def yeast_g(self):
        if self.yeast_mg is None:
            return None
        return Decimal(self.yeast_mg) / 1000

    @property
    def hydration_percent(self):
        if not self.flour_g or self.water_g is None:
            return None
        return Decimal(self.water_g) / self.flour_g * 100

    @property
    def salt_percent(self):
        if not self.flour_g or self.salt_g is None:
            return None
        return self.salt_g / self.flour_g * 100

    @property
    def fermentation_duration(self):
        return _duration(self.fermentation_started_at, self.fermentation_ended_at)

    @property
    def proofing_duration(self):
        return _duration(self.proofing_started_at, self.proofing_ended_at)

    @property
    def is_collecting(self):
        return self.collection_ended_at is None

    # Readings

    def dough_readings(self):
        return self._readings_for(self.dough_sensor_id)

    def ambient_readings(self):
        return self._readings_for(self.ambient_sensor_id)

    def _readings_for(self, sensor_id):
        if sensor_id is None:
            return Reading.objects.none()
        readings = Reading.objects.filter(sensor_id=sensor_id, recorded_at__gte=self.started_at)
        if self.collection_ended_at is not None:
            readings = readings.filter(recorded_at__lt=self.collection_ended_at)
        return readings

    # Button actions

    def start_fermentation(self):
        self._start('fermentation_started_at', 'Fermentation')

    def stop_fermentation(self):
        self._stop('fermentation_started_at', 'fermentation_ended_at', 'Fermentation')

    def start_proofing(self):
        self._start('proofing_started_at', 'Proofing')

    def stop_proofing(self):
        self._stop('proofing_started_at', 'proofing_ended_at', 'Proofing')

    def start_shaping(self):
        """Stop collecting readings for this dough."""
        if self.collection_ended_at is not None:
            raise ValidationError('Collection has already stopped.')
        self.collection_ended_at = timezone.now()
        self.save(update_fields=['collection_ended_at'])

    def _start(self, started_field, label):
        if getattr(self, started_field) is not None:
            raise ValidationError(f'{label} has already started.')
        setattr(self, started_field, timezone.now())
        self.save(update_fields=[started_field])

    def _stop(self, started_field, ended_field, label):
        if getattr(self, started_field) is None:
            raise ValidationError(f'{label} has not started.')
        if getattr(self, ended_field) is not None:
            raise ValidationError(f'{label} has already stopped.')
        setattr(self, ended_field, timezone.now())
        self.save(update_fields=[ended_field])


class Fold(models.Model):
    dough = models.ForeignKey(Dough, on_delete=models.CASCADE, related_name='folds')
    folded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['folded_at']

    def __str__(self):
        return f'Fold @ {timezone.localtime(self.folded_at):%H:%M}'


def _only_sensor(kind):
    sensors = list(Sensor.objects.filter(kind=kind)[:2])
    return sensors[0] if len(sensors) == 1 else None


def _duration(started_at, ended_at):
    if started_at is None or ended_at is None:
        return None
    return ended_at - started_at
