from django.urls import path
from . import views

app_name = 'ml_engine'

urlpatterns = [
    path('versions/', views.model_versions_view, name='versions'),
    path('retrain/', views.trigger_retrain_view, name='retrain'),
    path('buffer/', views.stream_buffer_view, name='buffer'),
    path('settings/', views.retrain_settings_view, name='retrain_settings'),
    path('mlflow/', views.mlflow_dashboard_view, name='mlflow_dashboard'),
    # Giai đoạn 2: Drift Monitoring + SHAP
    path('drift/', views.drift_report_view, name='drift_report'),
    path('drift/api/', views.drift_api_view, name='drift_api'),
    path('explanation/', views.model_explanation_view, name='model_explanation'),
    # Giai đoạn 3: DVC Data Versioning
    path('dvc/', views.dvc_status_view, name='dvc_status'),
    # Giai đoạn 4: Feast Feature Store
    path('feast/', views.feast_status_view, name='feast_status'),
    # Giai đoạn 5: FastAPI Inference Service
    path('fastapi/', views.fastapi_status_view, name='fastapi_status'),
]
