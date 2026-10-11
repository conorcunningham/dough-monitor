from django.contrib import messages
from django.db.models import Count
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from .forms import DoughForm
from .models import Dough, Fold, stray_readings
from sensors.models import Sensor

TIMER_ACTIONS = {
    'start-mixing': Dough.start_mixing,
    'start-fermentation': Dough.start_fermentation,
    'stop-fermentation': Dough.stop_fermentation,
    'start-proofing': Dough.start_proofing,
    'stop-proofing': Dough.stop_proofing,
    'start-shaping': Dough.start_shaping,
}


def is_htmx(request):
    return request.headers.get('HX-Request') == 'true'


@require_GET
def home(request):
    latest = Dough.objects.first()
    if latest is None:
        return redirect('doughs:list')
    return redirect('doughs:detail', pk=latest.pk)


@require_GET
def dough_list(request):
    return render(request, 'doughs/list.html', {'doughs': Dough.objects.select_related('dough_sensor')})


@require_POST
def dough_start(request):
    dough = Dough.start_new()
    return redirect('doughs:detail', pk=dough.pk)


def dough_detail(request, pk):
    dough = get_object_or_404(Dough, pk=pk)
    saved = False
    if request.method == 'POST':
        form = DoughForm(request.POST, instance=dough)
        if form.is_valid():
            form.save()
            if not is_htmx(request):
                return redirect('doughs:detail', pk=dough.pk)
            saved = True
            form = DoughForm(instance=dough)
        if is_htmx(request):
            return render(request, 'doughs/partials/form.html', {'dough': dough, 'form': form, 'saved': saved})
    else:
        form = DoughForm(instance=dough)
    return render(request, 'doughs/detail.html', {'dough': dough, 'form': form})


@require_POST
def dough_delete(request, pk):
    dough = get_object_or_404(Dough, pk=pk)
    dough.delete()
    messages.success(request, f'Deleted {dough}.')
    return redirect('doughs:list')


@require_POST
def timer_action(request, pk, action):
    dough = get_object_or_404(Dough, pk=pk)
    try:
        method = TIMER_ACTIONS[action]
    except KeyError:
        raise Http404(f'Unknown action {action!r}.')
    try:
        method(dough)
    except ValidationError as e:
        return _timers_response(request, dough, error=e.messages[0])
    return _timers_response(request, dough)


@require_POST
def fold_add(request, pk):
    dough = get_object_or_404(Dough, pk=pk)
    Fold.objects.create(dough=dough)
    return _timers_response(request, dough)


@require_POST
def fold_delete(request, pk, fold_pk):
    dough = get_object_or_404(Dough, pk=pk)
    get_object_or_404(Fold, pk=fold_pk, dough=dough).delete()
    return _timers_response(request, dough)


@require_GET
def latest_readings(request, pk):
    dough = get_object_or_404(Dough, pk=pk)
    return render(request, 'doughs/partials/latest.html', {'dough': dough})


@require_GET
def readings_data(request, pk):
    dough = get_object_or_404(Dough, pk=pk)
    ambient = list(dough.ambient_readings().values_list('recorded_at', 'temperature_c', 'humidity'))
    return JsonResponse({
        'dough': [_point(t, c) for t, c in dough.dough_readings().values_list('recorded_at', 'temperature_c')],
        'ambient': [_point(t, c) for t, c, _ in ambient],
        'humidity': [_point(t, h) for t, _, h in ambient if h is not None],
    })


def _point(recorded_at, value):
    # Chart.js plots on a linear axis of epoch milliseconds.
    return {'x': int(recorded_at.timestamp() * 1000), 'y': value}


def _timers_response(request, dough, error=None):
    if is_htmx(request):
        return render(request, 'doughs/partials/timers.html', {'dough': dough, 'error': error})
    if error:
        messages.error(request, error)
    return redirect('doughs:detail', pk=dough.pk)


@require_GET
def readings_overview(request):
    stray = stray_readings()
    sensors = Sensor.objects.annotate(reading_count=Count('readings')).order_by('kind', 'name')
    stray_per_sensor = dict(stray.values_list('sensor_id').annotate(n=Count('id')).values_list('sensor_id', 'n'))
    for sensor in sensors:
        sensor.stray_count = stray_per_sensor.get(sensor.pk, 0)
    return render(request, 'doughs/readings.html', {'sensors': sensors, 'stray_count': stray.count()})


@require_POST
def readings_prune(request):
    """Delete every reading that falls outside all doughs' collection windows."""
    deleted, _ = stray_readings().delete()
    messages.success(request, f'Deleted {deleted} reading{"s" if deleted != 1 else ""} outside any dough\'s collection window.')
    return redirect('doughs:readings_overview')
