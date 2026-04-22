from datetime import datetime
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.conf import settings
from PredictLOSWeb.mongodb import get_collection
from accounts.decorators import admin_required

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
