from datetime import datetime
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
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


# ──────────────────────────────────────────────────────────────
#  Giai đoạn 2: Drift Monitoring + SHAP Explainability
# ──────────────────────────────────────────────────────────────

@login_required
@admin_required
def drift_report_view(request):
    """Hiển thị drift report mới nhất và lịch sử 10 lần chạy gần nhất."""
    from .drift_monitor import get_latest_drift_report, get_drift_history

    if request.method == 'POST' and request.POST.get('action') == 'run_now':
        try:
            from .tasks import run_drift_monitoring
            run_drift_monitoring.delay()
            messages.success(request, 'Đã kích hoạt Evidently drift monitoring. Kết quả sẽ có sau vài giây.')
        except Exception:
            from .drift_monitor import run_evidently_report
            result = run_evidently_report(save_html=True)
            if result:
                score = result['overall_drift_score']
                level = result['drift_level'].upper()
                messages.success(request, f'Drift report hoàn tất: score={score:.3f} ({level})')
            else:
                messages.warning(request, 'Không đủ dữ liệu để chạy drift monitoring (cần ≥10 mẫu trong stream_buffer).')
        return redirect('ml_engine:drift_report')

    latest = get_latest_drift_report()
    history = get_drift_history(limit=10)

    drift_threshold_high = getattr(settings, 'EVIDENTLY_DRIFT_THRESHOLD_HIGH', 0.5)
    drift_threshold_medium = getattr(settings, 'EVIDENTLY_DRIFT_THRESHOLD_MEDIUM', 0.25)

    context = {
        'latest': latest,
        'history': history,
        'drift_threshold_high': drift_threshold_high,
        'drift_threshold_medium': drift_threshold_medium,
    }
    return render(request, 'ml_engine/drift_report.html', context)


@login_required
@admin_required
def model_explanation_view(request):
    """Hiển thị SHAP global feature importance của model đang active."""
    versions_col = get_collection('model_versions')

    # Ưu tiên version có SHAP mới nhất
    active = versions_col.find_one(
        {'is_active': True, 'shap_feature_importance': {'$exists': True, '$ne': None}}
    )
    if not active:
        active = versions_col.find_one({'is_active': True})

    shap_importance = None
    chart_b64 = None

    if active and active.get('shap_feature_importance'):
        shap_importance = active['shap_feature_importance']
        # Tạo chart on-the-fly
        try:
            from .shap_explainer import generate_shap_bar_chart, chart_to_base64
            chart_bytes = generate_shap_bar_chart(shap_importance)
            chart_b64 = chart_to_base64(chart_bytes)
        except Exception:
            pass

    # Nếu chưa có SHAP, tính lại ngay (chỉ khi có model SGD)
    if not shap_importance and request.method == 'POST' and request.POST.get('action') == 'compute_shap':
        try:
            from .trainer import retrain_model
            messages.info(request, 'Đang tính lại SHAP — quá trình này yêu cầu retrain model...')
        except Exception as exc:
            messages.error(request, f'Không thể tính SHAP: {exc}')

    all_versions = list(versions_col.find(
        {'shap_feature_importance': {'$exists': True, '$ne': None}},
        sort=[('trained_at', -1)],
        limit=5
    ))
    for v in all_versions:
        v['id_str'] = str(v['_id'])

    context = {
        'active_version': active,
        'shap_importance': shap_importance,
        'chart_b64': chart_b64,
        'versions_with_shap': all_versions,
    }
    if active:
        context['active_version_id'] = str(active['_id'])
    return render(request, 'ml_engine/model_explanation.html', context)


@login_required
@admin_required
def drift_api_view(request):
    """API endpoint: GET /ml/drift/api/?format=json|html

    Trả về drift report mới nhất dưới dạng JSON hoặc HTML.
    Tương đương /monitor/drift đã đề cập trong readme.
    """
    from .drift_monitor import get_latest_drift_report

    fmt = request.GET.get('format', 'json')
    report = get_latest_drift_report()

    if fmt == 'json':
        if report is None:
            return JsonResponse({'error': 'Chưa có drift report nào.'}, status=404)
        safe_report = {
            k: (str(v) if hasattr(v, '__class__') and v.__class__.__name__ == 'ObjectId' else v)
            for k, v in report.items()
            if k not in ('_id', 'html_report')
        }
        safe_report['run_at'] = report.get('run_at', '').isoformat() if report.get('run_at') else None
        return JsonResponse(safe_report)

    if fmt == 'html':
        if report and report.get('html_report'):
            return HttpResponse(report['html_report'], content_type='text/html')
        return HttpResponse('<h2>Chưa có HTML drift report.</h2>', content_type='text/html')

    return JsonResponse({'error': 'format không hợp lệ. Dùng ?format=json hoặc ?format=html'}, status=400)
