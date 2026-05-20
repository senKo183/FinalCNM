import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'PredictLOSWeb.settings')

app = Celery('PredictLOSWeb')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

app.conf.beat_schedule = {
    'daily-patient-alert-scan': {
        'task': 'ml_engine.tasks.daily_alert_scan',
        'schedule': crontab(hour=6, minute=0),
    },
    'check-retrain-conditions': {
        'task': 'ml_engine.tasks.check_retrain_conditions',
        'schedule': crontab(hour=7, minute=0),
    },
    # Giai đoạn 2: Evidently drift monitoring hàng tuần (Chủ nhật 02:00)
    'weekly-drift-monitoring': {
        'task': 'ml_engine.tasks.run_drift_monitoring',
        'schedule': crontab(hour=2, minute=0, day_of_week=0),
    },
    # Giai đoạn 4: Feast materialize hàng ngày 05:00 (đồng bộ Redis sau restart)
    'daily-feast-materialize': {
        'task': 'ml_engine.tasks.feast_materialize_admitted',
        'schedule': crontab(hour=5, minute=0),
    },
}
