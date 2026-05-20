"""Celery tasks — v2 Giai đoạn 4 Feast Feature Store.

Thêm mới so với Giai đoạn 2:
- trigger_retrain: log thêm dvc_snapshot_id vào kết quả trả về.
- run_dataset_dvc_track: task track file dataset gốc với DVC (thủ công).
"""
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
    """Check if retrain conditions are met and trigger if so.

    Giai đoạn 2: cũng kiểm tra drift report gần nhất — nếu drift HIGH,
    trigger retrain sớm bất kể điều kiện số mẫu.
    """
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

    # Kiểm tra drift cao → retrain sớm (Giai đoạn 2)
    drift_threshold = getattr(settings, 'EVIDENTLY_DRIFT_THRESHOLD_HIGH', 0.5)
    try:
        drift_col = get_collection('drift_reports')
        latest_drift = drift_col.find_one(
            {'triggered_retrain': False},
            sort=[('run_at', -1)]
        )
        if latest_drift and latest_drift.get('overall_drift_score', 0) >= drift_threshold:
            should_retrain = True
            reason = (
                f'Data drift cao: score={latest_drift["overall_drift_score"]:.3f} '
                f'>= ngưỡng {drift_threshold}'
            )
            # Đánh dấu đã trigger
            drift_col.update_one(
                {'_id': latest_drift['_id']},
                {'$set': {'triggered_retrain': True}}
            )
    except Exception:
        pass

    if not should_retrain:
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
    shap_info = ''
    if result.get('shap_feature_importance'):
        top1 = result['shap_feature_importance'][0]
        shap_info = f', top feature: {top1["feature"]} ({top1["importance"]:.4f})'
    dvc_info = ''
    if result.get('dvc_snapshot_id'):
        dvc_info = f', DVC snapshot: {result["dvc_snapshot_id"]}'
    return (
        f'Retrain hoàn tất ({reason}). '
        f'Version: {result["version"]}, MAE: {result["mae"]:.4f}, '
        f'{improved} version trước{shap_info}{dvc_info}.'
    )


@shared_task
def run_dataset_dvc_track():
    """Track file dataset gốc (LengthOfStay.csv, reference_data.csv) với DVC.

    Task thủ công — chạy sau khi cập nhật file dataset.
    Nên chạy qua: celery call ml_engine.tasks.run_dataset_dvc_track
    """
    from .dvc_manager import is_dvc_initialized, track_dataset_file
    if not is_dvc_initialized():
        return 'DVC chưa init. Bỏ qua.'

    results = []
    for path in [
        str(settings.CSV_DATA_PATH),
        str(settings.REFERENCE_DATA_PATH),
    ]:
        res = track_dataset_file(path)
        if res:
            results.append(f'{path} → md5={res["dvc_md5"][:16]}, pushed={res["pushed"]}')
        else:
            results.append(f'{path} → lỗi hoặc bỏ qua')

    return 'Dataset DVC track: ' + ' | '.join(results)


@shared_task
def feast_materialize_admitted():
    """Push features của tất cả bệnh nhân đang điều trị lên Feast Online Store.

    Task đồng bộ hóa hàng loạt — chạy thủ công hoặc theo lịch khi
    Redis khởi động lại (data bị mất) hoặc sau khi feast apply.
    Trả về số lượng đã push thành công.
    """
    from .feast_manager import push_patient_features, is_feast_available

    if not is_feast_available():
        return 'Feast chưa sẵn sàng (chưa install hoặc chưa feast apply). Bỏ qua.'

    patients_col = get_collection('patients')
    admitted = list(patients_col.find({'status': 'admitted'}))

    pushed = 0
    failed = 0
    for patient in admitted:
        features = dict(patient.get('features', {}))
        features['cccd'] = patient.get('cccd', '')
        if push_patient_features(features):
            pushed += 1
        else:
            failed += 1

    return (
        f'Feast materialize hoàn tất: đã push {pushed}/{len(admitted)} bệnh nhân '
        f'đang điều trị. Thất bại: {failed}.'
    )


@shared_task
def run_drift_monitoring():
    """Chạy Evidently drift report hàng tuần.

    Lưu kết quả vào drift_reports collection. Nếu drift HIGH, log cảnh báo
    và để check_retrain_conditions xử lý trigger retrain ở lần chạy tiếp theo.
    """
    from .drift_monitor import run_evidently_report
    result = run_evidently_report(save_html=True)

    if result is None:
        return 'Drift monitoring bỏ qua: không đủ dữ liệu hoặc lỗi.'

    score = result['overall_drift_score']
    level = result['drift_level']
    n_current = result['num_current_samples']
    n_ref = result['num_reference_samples']

    drift_threshold_high = getattr(settings, 'EVIDENTLY_DRIFT_THRESHOLD_HIGH', 0.5)
    auto_trigger_msg = ''
    if score >= drift_threshold_high:
        auto_trigger_msg = f' [CẢNH BÁO: drift HIGH={score:.3f} — sẽ trigger retrain ở lần kiểm tra tiếp theo]'

    return (
        f'Drift report hoàn tất. Score: {score:.3f} ({level.upper()}), '
        f'Mẫu: {n_current}/{n_ref} (current/reference).{auto_trigger_msg}'
    )
