from datetime import datetime, timedelta
from celery import shared_task
from PredictLOSWeb.mongodb import get_collection
from django.conf import settings


@shared_task
def daily_alert_scan():
    """Scan all admitted patients and update alert levels daily."""
    collection = get_collection('patients')
    patients = collection.find({'status': 'admitted'})
    updated = 0

    for patient in patients:
        predicted_discharge = patient.get('predicted_discharge_date')
        if not predicted_discharge:
            continue

        now = datetime.now()
        delta = (predicted_discharge - now).days

        if delta < 0:
            alert_level = 'danger'
        elif delta <= 2:
            alert_level = 'warning'
        else:
            alert_level = 'normal'

        if alert_level != patient.get('alert_level'):
            collection.update_one(
                {'_id': patient['_id']},
                {'$set': {'alert_level': alert_level, 'updated_at': now}}
            )
            updated += 1

    return f'Đã cập nhật cảnh báo cho {updated} bệnh nhân.'


@shared_task
def check_retrain_conditions():
    """Check if retrain conditions are met and trigger if so."""
    buffer = get_collection('stream_buffer')
    versions = get_collection('model_versions')

    pending_count = buffer.count_documents({'used_for_retrain': False})

    config_col = get_collection('system_config')
    config_doc = config_col.find_one({'_id': 'retrain_settings'})
    sample_threshold = (config_doc or {}).get(
        'retrain_threshold', settings.RETRAIN_SAMPLE_THRESHOLD
    )
    days_threshold = (config_doc or {}).get(
        'retrain_interval_days', settings.RETRAIN_DAYS_THRESHOLD
    )

    should_retrain = False
    reason = ''

    if pending_count >= sample_threshold:
        should_retrain = True
        reason = f'Đủ ngưỡng {pending_count}/{sample_threshold} mẫu'
    else:
        last_version = versions.find_one(
            {'trained_at': {'$ne': None}},
            sort=[('trained_at', -1)]
        )
        if last_version and last_version.get('trained_at'):
            days_since = (datetime.now() - last_version['trained_at']).days
            if days_since >= days_threshold and pending_count > 0:
                should_retrain = True
                reason = f'Đã {days_since} ngày kể từ lần retrain cuối ({pending_count} mẫu chờ)'
        elif pending_count > 0:
            should_retrain = True
            reason = f'Chưa có lần retrain nào ({pending_count} mẫu chờ)'

    if should_retrain:
        trigger_retrain.delay(reason)
        return f'Đã kích hoạt retrain: {reason}'

    return f'Chưa đủ điều kiện retrain. Mẫu chờ: {pending_count}/{sample_threshold}'


@shared_task
def trigger_retrain(reason='Thủ công'):
    """Execute the retrain process."""
    from .trainer import retrain_model
    result = retrain_model()
    improved = 'Tốt hơn' if result['improved'] else 'Không tốt hơn'
    return (
        f'Retrain hoàn tất ({reason}). '
        f'Version: {result["version"]}, MAE: {result["mae"]:.4f}, '
        f'{improved} version trước.'
    )
