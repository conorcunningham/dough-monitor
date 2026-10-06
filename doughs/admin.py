from django.contrib import admin

from .models import Dough, Fold


class FoldInline(admin.TabularInline):
    model = Fold
    extra = 0


@admin.register(Dough)
class DoughAdmin(admin.ModelAdmin):
    list_display = ['started_at', 'dough_sensor', 'flour_g', 'water_g', 'rating', 'rise']
    list_filter = ['rise', 'rating']
    date_hierarchy = 'started_at'
    inlines = [FoldInline]
