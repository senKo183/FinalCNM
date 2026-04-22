from datetime import datetime
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.conf import settings
from PredictLOSWeb.mongodb import get_collection
from accounts.decorators import admin_required
from .mlflow_config import is_mlflow_available, get_mlflow_client

SYSTEM_CONFIG_DEFAULTS = {
    'retrain_threshold': 10,
    'retrain_interval_days': 7,
}


def get_system_config():
    """Get system config from MongoDB, creating defaults if not exists."""
    config_col = get_collection('system_config')
    doc = config_col.find_one({'_id': 'retrain_settings'})
    if not doc:
        doc = {
            '_id': 'retrain_settings',
            'retrain_threshold': settings.RETRAIN_SAMPLE_THRESHOLD,
            'retrain_interval_days': settings.RETRAIN_DAYS_THRESHOLD,
            'updated_at': datetime.now(),
        }
        config_col.insert_one(doc)
    return doc


@login_required
def model_versions_view(request):
    versions = get_collection('model_versions')
    all_versions = list(versions.find().sort('trained_at', -1))

    buffer = get_collection('stream_buffer')
    pending_count = buffer.count_documents({'used_for_retrain': False})

    for v in all_versions:
        v['id_str'] = str(v['_id'])

    context = {
        'versions': all_versions,
        'pending_count': pending_count,
    }
    return render(request, 'ml_engine/versions.html', context)


@login_required
@admin_required
def trigger_retrain_view(request):
    if request.method == 'POST':
        from .tasks import trigger_retrain
        try:
            trigger_retrain.delay('Kích hoạt thủ công bởi ' + request.user.username)
            messages.success(request, 'Đã kích hoạt quá trình retrain. Vui lòng đợi kết quả.')
        except Exception:
            from .trainer import retrain_model
            result = retrain_model()
            improved = 'tốt hơn' if result['improved'] else 'không tốt hơn'
            messages.success(
                request,
                f'Retrain hoàn tất! Version {result["version"]}, '
                f'MAE: {result["mae"]:.4f} ({improved} version trước).'
            )
    return redirect('ml_engine:versions')


@login_required
def stream_buffer_view(request):
    buffer = get_collection('stream_buffer')
    items = list(buffer.find().sort('created_at', -1).limit(100))
    for item in items:
        item['id_str'] = str(item['_id'])

    context = {
        'items': items,
        'total_pending': buffer.count_documents({'used_for_retrain': False}),
        'total_used': buffer.count_documents({'used_for_retrain': True}),
    }
    return render(request, 'ml_engine/stream_buffer.html', context)


@login_required
@admin_required
def retrain_settings_view(request):
    config = get_system_config()

    if request.method == 'POST':
        try:
            threshold = int(request.POST.get('retrain_threshold', 10))
            interval = int(request.POST.get('retrain_interval_days', 7))
            if threshold < 1:
                raise ValueError('Ngưỡng phải >= 1')
            if interval < 1:
                raise ValueError('Số ngày phải >= 1')
        except (ValueError, TypeError) as e:
            messages.error(request, f'Giá trị không hợp lệ: {e}')
            return render(request, 'ml_engine/retrain_settings.html', {'config': config})

        config_col = get_collection('system_config')
        config_col.update_one(
            {'_id': 'retrain_settings'},
            {'$set': {
                'retrain_threshold': threshold,
                'retrain_interval_days': interval,
                'updated_at': datetime.now(),
                'updated_by': request.user.username,
            }},
            upsert=True,
        )
        messages.success(
            request,
            f'Đã cập nhật cấu hình retrain: ngưỡng = {threshold} mẫu, '
            f'thời gian chờ = {interval} ngày.'
        )
        return redirect('ml_engine:retrain_settings')

    buffer = get_collection('stream_buffer')
    pending_count = buffer.count_documents({'used_for_retrain': False})

    context = {
        'config': config,
        'pending_count': pending_count,
    }
    return render(request, 'ml_engine/retrain_settings.html', context)


@login_required
@admin_required
def mlflow_dashboard_view(request):
    """Admin panel: trạng thái MLflow + link UI + liệt kê 10 run gần nhất."""
    available = is_mlflow_available()
    recent_runs = []
    champion_version = None
    champion_run_id = None

    if available:
        client = get_mlflow_client()
        try:
            experiment = client.get_experiment_by_name(settings.MLFLOW_EXPERIMENT_NAME)
            if experiment is not None:
                runs = client.search_runs(
                    experiment_ids=[experiment.experiment_id],
                    order_by=['attributes.start_time DESC'],
                    max_results=10,
                )
                for run in runs:
                    recent_runs.append({
                        'run_id': run.info.run_id,
                        'run_name': run.data.tags.get('mlflow.runName', ''),
                        'status': run.info.status,
                        'start_time': datetime.fromtimestamp(run.info.start_time / 1000)
                            if run.info.start_time else None,
                        'mae': run.data.metrics.get('mae'),
                        'rmse': run.data.metrics.get('rmse'),
                        'r2': run.data.metrics.get('r2'),
                        'train_mode': run.data.tags.get('train_mode', ''),
                        'improved': run.data.tags.get('improved', ''),
                    })
        except Exception as exc:  # noqa: BLE001
            messages.warning(request, f'Không đọc được runs từ MLflow: {exc}')

        try:
            mv = client.get_model_version_by_alias(
                settings.MLFLOW_REGISTERED_MODEL_NAME, 'champion'
            )
            champion_version = mv.version
            champion_run_id = mv.run_id
        except Exception:
            pass

    context = {
        'mlflow_available': available,
        'mlflow_ui_url': settings.MLFLOW_TRACKING_URI,
        'experiment_name': settings.MLFLOW_EXPERIMENT_NAME,
        'registered_model_name': settings.MLFLOW_REGISTERED_MODEL_NAME,
        'recent_runs': recent_runs,
        'champion_version': champion_version,
        'champion_run_id': champion_run_id,
    }
    return render(request, 'ml_engine/mlflow_dashboard.html', context)
