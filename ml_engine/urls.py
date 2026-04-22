from django.urls import path
from . import views

app_name = 'ml_engine'

urlpatterns = [
    path('versions/', views.model_versions_view, name='versions'),
    path('retrain/', views.trigger_retrain_view, name='retrain'),
    path('buffer/', views.stream_buffer_view, name='buffer'),
    path('settings/', views.retrain_settings_view, name='retrain_settings'),
]
