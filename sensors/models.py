from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Sensor(models.Model):
    class Kind(models.TextChoices):
        DOUGH = 'dough', 'Dough'
        AMBIENT = 'ambient', 'Ambient'

    # Editable on purpose: many 433 MHz sensors pick a new ID when the battery
    # is swapped, so the ID is updated in place to keep the sensor's history.
    hardware_id = models.CharField(
        max_length=100,
        unique=True,
        help_text='DS18B20 ROM ID (e.g. 28-0123456789ab) or rtl_433 model/id.',
    )
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Reading(models.Model):
    sensor = models.ForeignKey(Sensor, on_delete=models.PROTECT, related_name='readings')
    # When the Pi took the reading, not when Django received it.
    recorded_at = models.DateTimeField()
    temperature_c = models.FloatField()
    humidity = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )

    class Meta:
        ordering = ['recorded_at']
        constraints = [
            # MQTT QoS 1 can redeliver a message; the same reading must not be stored twice.
            models.UniqueConstraint(fields=['sensor', 'recorded_at'], name='unique_reading_per_sensor_time'),
            models.CheckConstraint(
                condition=models.Q(humidity__isnull=True) | models.Q(humidity__gte=0, humidity__lte=100),
                name='reading_humidity_0_to_100',
            ),
        ]

    def __str__(self):
        return f'{self.sensor} {self.temperature_c}°C @ {self.recorded_at:%Y-%m-%d %H:%M:%S}'
