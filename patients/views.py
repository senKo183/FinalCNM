import re
from datetime import datetime, timedelta
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.conf import settings
from bson import ObjectId
from PredictLOSWeb.mongodb import get_collection
from ml_engine.predictor import predict_los


COMORBIDITY_FIELDS = [
    ('dialysisrenalendstage', 'Suy thận giai đoạn cuối'),
    ('asthma', 'Hen suyễn'),
    ('irondef', 'Thiếu sắt'),
    ('pneum', 'Viêm phổi'),
    ('substancedependence', 'Lệ thuộc chất kích thích'),
    ('psychologicaldisordermajor', 'Rối loạn tâm thần'),
    ('depress', 'Trầm cảm'),
    ('psychother', 'Điều trị tâm lý'),
    ('fibrosisandother', 'Xơ hóa'),
    ('malnutrition', 'Suy dinh dưỡng'),
    ('hemo', 'Rối loạn huyết sắc tố'),
]

FACILITY_CHOICES = ['A', 'B', 'C', 'D', 'E']

PHONE_REGEX = re.compile(r'^0\d{9}$')


def _validate_phone(phone):
    """Validate Vietnamese phone number: 10 digits starting with 0."""
    if not phone:
        return True
    return bool(PHONE_REGEX.match(phone))


def _generate_eid():
    """Tự động sinh mã bệnh nhân: PT-YYYYMMDD-XXXX."""
    collection = get_collection('patients')
    today_str = datetime.now().strftime('%Y%m%d')
    prefix = f'PT-{today_str}-'

    last_patient = collection.find_one(
        {'patient_id': {'$regex': f'^{prefix}'}},
        sort=[('patient_id', -1)],
    )
    if last_patient:
        last_seq = int(last_patient['patient_id'].split('-')[-1])
        new_seq = last_seq + 1
    else:
        new_seq = 1

    return f'{prefix}{new_seq:04d}'


def _count_rcount(cccd):
    """Đếm số hồ sơ đã xuất viện có cùng CCCD để tính rcount."""
    if not cccd:
        return 0
    collection = get_collection('patients')
    count = collection.count_documents({
        'cccd': cccd,
        'status': 'discharged',
    })
    return min(count, 5)


def _compute_alert_level(predicted_discharge_date):
    if predicted_discharge_date is None:
        return 'normal'
    now = datetime.now()
    delta = (predicted_discharge_date - now).days
    if delta < 0:
        return 'danger'
    elif delta <= 2:
        return 'warning'
    return 'normal'


@login_required
def patient_list(request):
    collection = get_collection('patients')

    query = {}
    status_filter = request.GET.get('status', '')
    facid_filter = request.GET.get('facid', '')
    search = request.GET.get('search', '')
    alert_filter = request.GET.get('alert', '')

    if status_filter:
        query['status'] = status_filter
    if facid_filter:
        query['facid'] = facid_filter
    if search:
        query['$or'] = [
            {'patient_name': {'$regex': search, '$options': 'i'}},
            {'patient_id': {'$regex': search, '$options': 'i'}},
            {'cccd': {'$regex': search, '$options': 'i'}},
        ]
    if alert_filter:
        query['alert_level'] = alert_filter

    patients = list(collection.find(query).sort('created_at', -1))

    for p in patients:
        p['id_str'] = str(p['_id'])
        if p.get('admission_date'):
            days_admitted = (datetime.now() - p['admission_date']).days
            p['days_admitted'] = max(days_admitted, 0)
            if p.get('predicted_los'):
                remaining = p['predicted_los'] - days_admitted
                p['days_remaining'] = round(max(remaining, 0), 2)
            else:
                p['days_remaining'] = None
        else:
            p['days_admitted'] = 0
            p['days_remaining'] = None

    context = {
        'patients': patients,
        'status_filter': status_filter,
        'facid_filter': facid_filter,
        'search': search,
        'alert_filter': alert_filter,
        'facility_choices': FACILITY_CHOICES,
    }
    return render(request, 'patients/list.html', context)


@login_required
def patient_create(request):
    if request.method == 'POST':
        collection = get_collection('patients')

        admission_date_str = request.POST.get('admission_date', '')
        try:
            admission_date = datetime.strptime(admission_date_str, '%Y-%m-%dT%H:%M')
        except (ValueError, TypeError):
            admission_date = datetime.now()

        cccd = request.POST.get('cccd', '').strip()
        rcount = _count_rcount(cccd)

        dob_str = request.POST.get('date_of_birth', '')
        try:
            date_of_birth = datetime.strptime(dob_str, '%Y-%m-%d')
        except (ValueError, TypeError):
            date_of_birth = None

        try:
            secondary_raw = request.POST.get('secondarydiagnosisnonicd9', '')
            if secondary_raw.strip() == '':
                messages.error(request, 'Trường chẩn đoán phụ ngoài ICD-9 là bắt buộc.')
                return render(request, 'patients/create.html', {
                    'comorbidity_fields': COMORBIDITY_FIELDS,
                    'facility_choices': FACILITY_CHOICES,
                })
            secondary_val = float(secondary_raw)
            if secondary_val != int(secondary_val) or secondary_val < 0 or secondary_val > 10:
                raise ValueError
            secondary_val = int(secondary_val)
        except (ValueError, TypeError):
            messages.error(request, 'Chẩn đoán phụ ngoài ICD-9 phải là số nguyên từ 0 đến 10.')
            return render(request, 'patients/create.html', {
                'comorbidity_fields': COMORBIDITY_FIELDS,
                'facility_choices': FACILITY_CHOICES,
            })

        phone = request.POST.get('phone', '').strip()
        if phone and not _validate_phone(phone):
            messages.error(request, 'Số điện thoại phải đúng 10 chữ số và bắt đầu bằng số 0.')
            return render(request, 'patients/create.html', {
                'comorbidity_fields': COMORBIDITY_FIELDS,
                'facility_choices': FACILITY_CHOICES,
            })

        features = {
            'rcount': rcount,
            'hematocrit': float(request.POST.get('hematocrit', 0)),
            'neutrophils': float(request.POST.get('neutrophils', 0)),
            'sodium': float(request.POST.get('sodium', 0)),
            'glucose': float(request.POST.get('glucose', 0)),
            'bloodureanitro': float(request.POST.get('bloodureanitro', 0)),
            'creatinine': float(request.POST.get('creatinine', 0)),
            'bmi': float(request.POST.get('bmi', 0)),
            'pulse': float(request.POST.get('pulse', 0)),
            'respiration': float(request.POST.get('respiration', 0)),
            'secondarydiagnosisnonicd9': secondary_val,
        }

        for field_name, _ in COMORBIDITY_FIELDS:
            features[field_name] = 1 if request.POST.get(field_name) else 0

        prediction_result = predict_los(features)
        predicted_los = prediction_result['predicted_los']
        predicted_discharge_date = admission_date + timedelta(days=round(predicted_los))

        patient = {
            'patient_id': _generate_eid(),
            'patient_name': request.POST.get('patient_name', ''),
            'cccd': cccd,
            'date_of_birth': date_of_birth,
            'phone': phone,
            'facid': request.POST.get('facid', 'A'),
            'gender': request.POST.get('gender', 'M'),
            'admission_date': admission_date,
            'features': features,
            'predicted_los': round(predicted_los, 2),
            'predicted_discharge_date': predicted_discharge_date,
            'model_version_used': prediction_result.get('model_version', 'v1'),
            'status': 'admitted',
            'alert_level': _compute_alert_level(predicted_discharge_date),
            'discharge_date': None,
            'discharge_status': None,
            'actual_los': None,
            'prediction_error': None,
            'created_by': request.user.username,
            'created_at': datetime.now(),
            'updated_at': datetime.now(),
        }

        result = collection.insert_one(patient)
        messages.success(
            request,
            f'Tạo hồ sơ thành công — Mã EID: {patient["patient_id"]}. '
            f'Dự đoán LOS: {predicted_los:.1f} ngày. '
            f'Dự kiến xuất viện: {predicted_discharge_date.strftime("%d/%m/%Y")}.'
        )
        return redirect('patients:detail', patient_id=str(result.inserted_id))

    context = {
        'comorbidity_fields': COMORBIDITY_FIELDS,
        'facility_choices': FACILITY_CHOICES,
    }
    return render(request, 'patients/create.html', context)


@login_required
def patient_detail(request, patient_id):
    collection = get_collection('patients')
    patient = collection.find_one({'_id': ObjectId(patient_id)})

    if not patient:
        messages.error(request, 'Không tìm thấy hồ sơ bệnh nhân.')
        return redirect('patients:list')

    patient['id_str'] = str(patient['_id'])
    if patient.get('admission_date'):
        patient['days_admitted'] = max((datetime.now() - patient['admission_date']).days, 0)
    else:
        patient['days_admitted'] = 0

    context = {
        'patient': patient,
        'comorbidity_fields': COMORBIDITY_FIELDS,
    }
    return render(request, 'patients/detail.html', context)


@login_required
def patient_discharge(request, patient_id):
    collection = get_collection('patients')
    patient = collection.find_one({'_id': ObjectId(patient_id)})

    if not patient:
        messages.error(request, 'Không tìm thấy hồ sơ bệnh nhân.')
        return redirect('patients:list')

    if patient.get('status') == 'discharged':
        messages.warning(request, 'Bệnh nhân đã được xuất viện trước đó.')
        return redirect('patients:detail', patient_id=patient_id)

    if request.method == 'POST':
        discharge_date_str = request.POST.get('discharge_date', '')
        try:
            discharge_date = datetime.strptime(discharge_date_str, '%Y-%m-%dT%H:%M')
        except (ValueError, TypeError):
            discharge_date = datetime.now()

        if discharge_date > datetime.now():
            messages.error(request, 'Ngày xuất viện không được chọn thời điểm ở tương lai.')
            patient['id_str'] = str(patient['_id'])
            return render(request, 'patients/discharge.html', {'patient': patient})

        if patient.get('admission_date') and discharge_date < patient['admission_date']:
            messages.error(request, 'Ngày xuất viện phải lớn hơn hoặc bằng ngày nhập viện.')
            patient['id_str'] = str(patient['_id'])
            return render(request, 'patients/discharge.html', {'patient': patient})

        discharge_status = request.POST.get('discharge_status', 'recovered')

        actual_los = (discharge_date - patient['admission_date']).total_seconds() / 86400
        actual_los = max(round(actual_los, 2), 0)
        prediction_error = actual_los - patient.get('predicted_los', 0)

        collection.update_one(
            {'_id': ObjectId(patient_id)},
            {'$set': {
                'status': 'discharged',
                'discharge_date': discharge_date,
                'discharge_status': discharge_status,
                'actual_los': actual_los,
                'prediction_error': round(prediction_error, 2),
                'alert_level': 'normal',
                'updated_at': datetime.now(),
            }}
        )

        buffer_collection = get_collection('stream_buffer')
        buffer_collection.insert_one({
            'patient_id': str(patient['_id']),
            'features': patient.get('features', {}),
            'actual_los': actual_los,
            'used_for_retrain': False,
            'created_at': datetime.now(),
        })

        messages.success(
            request,
            f'Xuất viện thành công. LOS thực tế: {actual_los:.1f} ngày. '
            f'Sai lệch: {prediction_error:+.1f} ngày so với dự đoán.'
        )
        return redirect('patients:detail', patient_id=patient_id)

    patient['id_str'] = str(patient['_id'])
    return render(request, 'patients/discharge.html', {'patient': patient})


@login_required
def patient_edit(request, patient_id):
    collection = get_collection('patients')
    patient = collection.find_one({'_id': ObjectId(patient_id)})

    if not patient:
        messages.error(request, 'Không tìm thấy hồ sơ bệnh nhân.')
        return redirect('patients:list')

    if patient.get('status') == 'discharged':
        messages.warning(request, 'Không thể chỉnh sửa hồ sơ bệnh nhân đã xuất viện.')
        return redirect('patients:detail', patient_id=patient_id)

    patient['id_str'] = str(patient['_id'])

    if request.method == 'POST':
        phone = request.POST.get('phone', '').strip()
        if phone and not _validate_phone(phone):
            messages.error(request, 'Số điện thoại phải đúng 10 chữ số và bắt đầu bằng số 0.')
            return render(request, 'patients/edit.html', {
                'patient': patient,
                'comorbidity_fields': COMORBIDITY_FIELDS,
                'facility_choices': FACILITY_CHOICES,
            })

        try:
            secondary_raw = request.POST.get('secondarydiagnosisnonicd9', '')
            if secondary_raw.strip() == '':
                raise ValueError
            secondary_val = float(secondary_raw)
            if secondary_val != int(secondary_val) or secondary_val < 0 or secondary_val > 10:
                raise ValueError
            secondary_val = int(secondary_val)
        except (ValueError, TypeError):
            messages.error(request, 'Chẩn đoán phụ ngoài ICD-9 phải là số nguyên từ 0 đến 10.')
            return render(request, 'patients/edit.html', {
                'patient': patient,
                'comorbidity_fields': COMORBIDITY_FIELDS,
                'facility_choices': FACILITY_CHOICES,
            })

        dob_str = request.POST.get('date_of_birth', '')
        try:
            date_of_birth = datetime.strptime(dob_str, '%Y-%m-%d')
        except (ValueError, TypeError):
            date_of_birth = patient.get('date_of_birth')

        old_features = patient.get('features', {})
        rcount = old_features.get('rcount', 0)

        new_features = {
            'rcount': rcount,
            'hematocrit': float(request.POST.get('hematocrit', 0)),
            'neutrophils': float(request.POST.get('neutrophils', 0)),
            'sodium': float(request.POST.get('sodium', 0)),
            'glucose': float(request.POST.get('glucose', 0)),
            'bloodureanitro': float(request.POST.get('bloodureanitro', 0)),
            'creatinine': float(request.POST.get('creatinine', 0)),
            'bmi': float(request.POST.get('bmi', 0)),
            'pulse': float(request.POST.get('pulse', 0)),
            'respiration': float(request.POST.get('respiration', 0)),
            'secondarydiagnosisnonicd9': secondary_val,
        }
        for field_name, _ in COMORBIDITY_FIELDS:
            new_features[field_name] = 1 if request.POST.get(field_name) else 0

        features_changed = any(
            new_features.get(k) != old_features.get(k)
            for k in new_features
        )

        update_fields = {
            'patient_name': request.POST.get('patient_name', patient.get('patient_name', '')),
            'phone': phone,
            'date_of_birth': date_of_birth,
            'facid': request.POST.get('facid', patient.get('facid', 'A')),
            'features': new_features,
            'updated_at': datetime.now(),
        }

        if features_changed:
            prediction_result = predict_los(new_features)
            predicted_los = prediction_result['predicted_los']
            admission_date = patient['admission_date']
            predicted_discharge_date = admission_date + timedelta(days=round(predicted_los))
            update_fields['predicted_los'] = round(predicted_los, 2)
            update_fields['predicted_discharge_date'] = predicted_discharge_date
            update_fields['model_version_used'] = prediction_result.get('model_version', 'v1')
            update_fields['alert_level'] = _compute_alert_level(predicted_discharge_date)

        collection.update_one(
            {'_id': ObjectId(patient_id)},
            {'$set': update_fields}
        )

        if features_changed:
            messages.success(
                request,
                f'Cập nhật hồ sơ thành công. Features đã thay đổi — LOS dự đoán mới: '
                f'{predicted_los:.1f} ngày.'
            )
        else:
            messages.success(request, 'Cập nhật hồ sơ thành công.')

        return redirect('patients:detail', patient_id=patient_id)

    context = {
        'patient': patient,
        'comorbidity_fields': COMORBIDITY_FIELDS,
        'facility_choices': FACILITY_CHOICES,
    }
    return render(request, 'patients/edit.html', context)


@login_required
@require_GET
def lookup_cccd(request):
    """API tra cứu CCCD realtime — trả về rcount và lịch sử nhập viện."""
    cccd = request.GET.get('cccd', '').strip()
    if not cccd:
        return JsonResponse({'rcount': 0, 'history': [], 'found': False})

    collection = get_collection('patients')
    past_records = list(collection.find(
        {'cccd': cccd, 'status': 'discharged'},
    ).sort('admission_date', -1))

    rcount = min(len(past_records), 5)

    history = []
    for rec in past_records:
        history.append({
            'patient_id': rec.get('patient_id', ''),
            'admission_date': rec['admission_date'].strftime('%d/%m/%Y') if rec.get('admission_date') else '',
            'discharge_date': rec['discharge_date'].strftime('%d/%m/%Y') if rec.get('discharge_date') else '',
            'actual_los': rec.get('actual_los'),
            'discharge_status': rec.get('discharge_status', ''),
        })

    current_admitted = collection.find_one({'cccd': cccd, 'status': 'admitted'})

    return JsonResponse({
        'found': len(past_records) > 0,
        'rcount': rcount,
        'total_past_admissions': len(past_records),
        'history': history,
        'currently_admitted': current_admitted is not None,
    })
