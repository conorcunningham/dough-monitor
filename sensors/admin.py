from django.contrib import admin

from .models import Reading, Sensor


@admin.register(Sensor)
class SensorAdmin(admin.ModelAdmin):
    list_display = ['name', 'kind', 'hardware_id', 'created_at']
    list_filter = ['kind']
    search_fields = ['name', 'hardware_id']


@admin.register(Reading)
class ReadingAdmin(admin.ModelAdmin):
    list_display = ['recorded_at', 'sensor', 'temperature_c', 'humidity']
    list_filter = ['sensor']
    date_hierarchy = 'recorded_at'
