from datetime import timedelta

from django import template
from django.utils import timezone

register = template.Library()

STALE_AFTER = timedelta(minutes=10)


@register.filter
def duration(value):
    """Format a timedelta as e.g. '10h 05m', or '45m' when under an hour."""
    if value is None:
        return ''
    minutes = int(value.total_seconds() // 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f'{hours}h {minutes:02d}m'
    return f'{minutes}m'


@register.filter
def is_stale(reading):
    """True when a sensor has gone quiet for longer than STALE_AFTER."""
    return reading is not None and timezone.now() - reading.recorded_at > STALE_AFTER
