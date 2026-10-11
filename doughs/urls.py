from django.urls import path

from . import views

app_name = 'doughs'

urlpatterns = [
    path('', views.home, name='home'),
    path('doughs/', views.dough_list, name='list'),
    path('doughs/new/', views.dough_start, name='start'),
    path('doughs/<int:pk>/', views.dough_detail, name='detail'),
    path('doughs/<int:pk>/timers/<slug:action>/', views.timer_action, name='timer'),
    path('doughs/<int:pk>/folds/', views.fold_add, name='fold_add'),
    path('doughs/<int:pk>/folds/<int:fold_pk>/delete/', views.fold_delete, name='fold_delete'),
    path('doughs/<int:pk>/latest/', views.latest_readings, name='latest'),
    path('doughs/<int:pk>/readings/', views.readings_data, name='readings'),
    path('readings/', views.readings_overview, name='readings_overview'),
    path('readings/prune/', views.readings_prune, name='readings_prune'),
]
