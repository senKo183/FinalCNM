from datetime import datetime, timedelta
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from PredictLOSWeb.mongodb import get_collection


@login_required
def index(request):
    patients = get_collection('patients')
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    total_admitted = patients.count_documents({'status': 'admitted'})
    discharged_today = patients.count_documents({
        'status': 'discharged',
        'discharge_date': {'$gte': today_start},
    })
    overdue_count = patients.count_documents({
        'status': 'admitted',
        'alert_level': 'danger',
    })
    warning_count = patients.count_documents({
        'status': 'admitted',
        'alert_level': 'warning',
    })

    month_discharged = list(patients.find({
        'status': 'discharged',
        'discharge_date': {'$gte': month_start},
        'prediction_error': {'$exists': True, '$ne': None},
    }))

    if month_discharged:
        avg_error = sum(abs(p['prediction_error']) for p in month_discharged) / len(month_discharged)
    else:
        avg_error = 0

    alert_patients = list(patients.find({
        'status': 'admitted',
        'alert_level': {'$in': ['warning', 'danger']},
    }).sort('alert_level', 1).limit(20))

    for p in alert_patients:
        p['id_str'] = str(p['_id'])
        if p.get('admission_date'):
            p['days_admitted'] = max((now - p['admission_date']).days, 0)

    buffer = get_collection('stream_buffer')
    pending_retrain = buffer.count_documents({'used_for_retrain': False})

    versions = get_collection('model_versions')
    active_model = versions.find_one({'is_active': True})

    context = {
        'total_admitted': total_admitted,
        'discharged_today': discharged_today,
        'overdue_count': overdue_count,
        'warning_count': warning_count,
        'avg_error': round(avg_error, 2),
        'alert_patients': alert_patients,
        'pending_retrain': pending_retrain,
        'active_model': active_model,
    }
    return render(request, 'dashboard/index.html', context)


@login_required
def chart_data(request):
    """API endpoint for chart data."""
    patients = get_collection('patients')
    chart_type = request.GET.get('type', 'los_comparison')

    if chart_type == 'los_comparison':
        discharged = list(patients.find({
            'status': 'discharged',
            'actual_los': {'$exists': True, '$ne': None},
            'predicted_los': {'$exists': True, '$ne': None},
        }).sort('discharge_date', -1).limit(50))

        data = {
            'labels': [p.get('patient_name', p.get('patient_id', '?')) for p in discharged],
            'predicted': [p.get('predicted_los', 0) for p in discharged],
            'actual': [p.get('actual_los', 0) for p in discharged],
        }

    elif chart_type == 'error_distribution':
        discharged = list(patients.find({
            'status': 'discharged',
            'prediction_error': {'$exists': True, '$ne': None},
        }))

        errors = [p['prediction_error'] for p in discharged]
        bins = list(range(-10, 11))
        counts = [0] * len(bins)
        for e in errors:
            for i, b in enumerate(bins):
                if b - 0.5 <= e < b + 0.5:
                    counts[i] += 1
                    break

        data = {'labels': bins, 'counts': counts}

    elif chart_type == 'model_performance':
        versions = get_collection('model_versions')
        all_versions = list(versions.find().sort('trained_at', 1))
        data = {
            'labels': [v.get('version_number', '') for v in all_versions],
            'mae': [v.get('mae', 0) for v in all_versions],
            'rmse': [v.get('rmse', 0) for v in all_versions],
            'r2': [v.get('r2_score', 0) for v in all_versions],
        }
    else:
        data = {}

    return JsonResponse(data)
