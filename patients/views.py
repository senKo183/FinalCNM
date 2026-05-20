import csv
import io
import re
from datetime import datetime, timedelta
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_GET
from django.conf import settings
from bson import ObjectId
from PredictLOSWeb.mongodb import get_collection
from ml_engine.predictor import predict_los


def _push_to_feast(patient_doc: dict) -> None:
    """Push features lên Feast Online Store sau khi tạo/cập nhật bệnh nhân.
    Fail gracefully — không raise exception nếu Feast chưa sẵn sàng.
    """
    try:
        from ml_engine.feast_manager import push_patient_features  # noqa: WPS433
        push_patient_features(patient_doc)
    except Exception:  # noqa: BLE001
        pass


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

        prediction_result = predict_los(features, cccd=cccd)
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

        # Giai đoạn 4: Push features lên Feast Online Store (Redis)
        _push_to_feast({**features, 'cccd': cccd, 'rcount': features['rcount']})

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

    # SHAP per-patient explanation (Giai đoạn 2)
    shap_explanation = _get_patient_shap_explanation(patient)

    context = {
        'patient': patient,
        'comorbidity_fields': COMORBIDITY_FIELDS,
        'shap_explanation': shap_explanation,
    }
    return render(request, 'patients/detail.html', context)


def _get_patient_shap_explanation(patient):
    """Tính SHAP explanation cho bệnh nhân cụ thể. Fail graceful.

    Dùng background nhỏ (200 dòng) để tránh lag trang chi tiết bệnh nhân.
    """
    try:
        from ml_engine.predictor import preprocess_features, FEATURE_ORDER
        from ml_engine.predictor import CONTINUOUS_VARS, ISSUE_COLUMNS
        from ml_engine.shap_explainer import explain_patient
        from ml_engine.predictor import _get_active_model
        import numpy as np
        import pandas as pd
        from django.conf import settings

        model, _ = _get_active_model()
        if model is None:
            return None

        raw_features = dict(patient.get('features', {}))
        processed = preprocess_features(raw_features)

        csv_path = settings.CSV_DATA_PATH
        if not csv_path or not csv_path.exists():
            return None

        df = pd.read_csv(csv_path, nrows=500)  # Chỉ đọc 500 dòng làm background
        df['rcount'] = pd.to_numeric(
            df['rcount'].astype(str).str.replace('+', '', regex=False),
            errors='coerce'
        ).fillna(0)

        for col in CONTINUOUS_VARS:
            mean = df[col].mean()
            std = df[col].std()
            if std > 0:
                df[col] = (df[col] - mean) / std
        for col in ISSUE_COLUMNS:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        df['number_of_issues'] = df[ISSUE_COLUMNS].sum(axis=1)

        X_bg = df[FEATURE_ORDER].values

        return explain_patient(model, X_bg, processed, top_n=5)
    except Exception:
        return None


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

        # Giai đoạn 4: Cập nhật Feast Online Store — rcount tăng sau xuất viện
        new_rcount = _count_rcount(patient.get('cccd', ''))
        updated_features = dict(patient.get('features', {}))
        updated_features['rcount'] = new_rcount
        _push_to_feast({**updated_features, 'cccd': patient.get('cccd', '')})

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
            prediction_result = predict_los(new_features, cccd=patient.get('cccd'))
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

        # Giai đoạn 4: Push features mới lên Feast Online Store (Redis)
        _push_to_feast({**new_features, 'cccd': patient.get('cccd', '')})

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


# ──────────────────────────────────────────────────────────────
#  Import CSV bệnh nhân hàng loạt
# ──────────────────────────────────────────────────────────────

CSV_TEMPLATE_HEADERS = [
    'patient_name', 'cccd', 'date_of_birth', 'phone', 'gender', 'facid',
    'admission_date',
    'hematocrit', 'neutrophils', 'sodium', 'glucose', 'bloodureanitro',
    'creatinine', 'bmi', 'pulse', 'respiration',
    'secondarydiagnosisnonicd9',
    'dialysisrenalendstage', 'asthma', 'irondef', 'pneum',
    'substancedependence', 'psychologicaldisordermajor', 'depress',
    'psychother', 'fibrosisandother', 'malnutrition', 'hemo',
]

CSV_REQUIRED_COLS = {'patient_name', 'cccd', 'hematocrit', 'neutrophils', 'sodium',
                     'glucose', 'bloodureanitro', 'creatinine', 'bmi', 'pulse',
                     'respiration', 'secondarydiagnosisnonicd9'}

NUMERIC_COLS = [
    'hematocrit', 'neutrophils', 'sodium', 'glucose', 'bloodureanitro',
    'creatinine', 'bmi', 'pulse', 'respiration',
]

BINARY_COLS = [
    'dialysisrenalendstage', 'asthma', 'irondef', 'pneum',
    'substancedependence', 'psychologicaldisordermajor', 'depress',
    'psychother', 'fibrosisandother', 'malnutrition', 'hemo',
]


@login_required
def download_csv_template(request):
    """Trả về file CSV mẫu để người dùng download và điền dữ liệu."""
    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = 'attachment; filename="patient_import_template.csv"'
    response.write('\ufeff')  # BOM cho Excel đọc đúng UTF-8

    writer = csv.writer(response)
    writer.writerow(CSV_TEMPLATE_HEADERS)

    # 2 hàng ví dụ
    writer.writerow([
        'Nguyễn Văn A', '001234567890', '1985-03-15', '0912345678', 'M', 'A',
        '2026-04-23 08:00',
        38.5, 65.0, 138.0, 100.0, 18.0, 1.0, 24.5, 80.0, 18.0,
        2,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    ])
    writer.writerow([
        'Trần Thị B', '098765432100', '1970-07-20', '0987654321', 'F', 'B',
        '2026-04-22 14:30',
        36.0, 70.0, 140.0, 95.0, 22.0, 1.2, 28.0, 88.0, 20.0,
        1,
        1, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0,
    ])
    return response


def _parse_csv_row(row: dict, row_num: int) -> tuple[dict | None, list[str]]:
    """Parse và validate một hàng CSV. Trả về (patient_doc, errors)."""
    errors = []

    # Required fields
    patient_name = row.get('patient_name', '').strip()
    cccd = row.get('cccd', '').strip()
    if not patient_name:
        errors.append('Thiếu họ tên')
    if not cccd:
        errors.append('Thiếu CCCD')

    # Phone validation
    phone = row.get('phone', '').strip()
    if phone and not PHONE_REGEX.match(phone):
        errors.append('SĐT không hợp lệ (cần 10 chữ số bắt đầu bằng 0)')

    # Gender
    gender = row.get('gender', 'M').strip().upper()
    if gender not in ('M', 'F'):
        gender = 'M'

    # Facility
    facid = row.get('facid', 'A').strip().upper()
    if facid not in FACILITY_CHOICES:
        facid = 'A'

    # Date of birth
    dob_str = row.get('date_of_birth', '').strip()
    date_of_birth = None
    if dob_str:
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
            try:
                date_of_birth = datetime.strptime(dob_str, fmt)
                break
            except ValueError:
                continue
        if date_of_birth is None:
            errors.append(f'Ngày sinh không hợp lệ: {dob_str!r} (dùng YYYY-MM-DD)')

    # Admission date
    adm_str = row.get('admission_date', '').strip()
    admission_date = None
    if adm_str:
        for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M', '%d/%m/%Y %H:%M', '%Y-%m-%d'):
            try:
                admission_date = datetime.strptime(adm_str, fmt)
                break
            except ValueError:
                continue
    if admission_date is None:
        admission_date = datetime.now()

    # Numeric features
    features = {}
    for col in NUMERIC_COLS:
        val_str = row.get(col, '').strip()
        try:
            features[col] = float(val_str)
        except (ValueError, TypeError):
            errors.append(f'Giá trị không hợp lệ cho cột {col}: {val_str!r}')
            features[col] = 0.0

    # Secondary diagnosis
    sec_str = row.get('secondarydiagnosisnonicd9', '').strip()
    try:
        sec_val = float(sec_str)
        if sec_val != int(sec_val) or sec_val < 0 or sec_val > 10:
            raise ValueError
        features['secondarydiagnosisnonicd9'] = int(sec_val)
    except (ValueError, TypeError):
        errors.append(f'secondarydiagnosisnonicd9 phải là số nguyên 0–10, nhận được: {sec_str!r}')
        features['secondarydiagnosisnonicd9'] = 0

    # Binary comorbidities
    for col in BINARY_COLS:
        val_str = row.get(col, '0').strip()
        features[col] = 1 if val_str in ('1', 'true', 'True', 'yes', 'Yes') else 0

    if errors:
        return None, errors

    # rcount tự động từ CCCD
    features['rcount'] = _count_rcount(cccd)

    # Dự đoán LOS
    try:
        prediction_result = predict_los(features)
        predicted_los = prediction_result['predicted_los']
    except Exception as exc:
        return None, [f'Lỗi dự đoán LOS: {exc}']

    predicted_discharge_date = admission_date + timedelta(days=round(predicted_los))

    patient_doc = {
        'patient_id': None,  # sẽ gán sau khi insert hàng loạt
        'patient_name': patient_name,
        'cccd': cccd,
        'date_of_birth': date_of_birth,
        'phone': phone,
        'gender': gender,
        'facid': facid,
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
    }
    return patient_doc, []


@login_required
def import_csv_view(request):
    """Import danh sách bệnh nhân từ file CSV.

    GET  → hiển thị form upload + download template
    POST → parse CSV, validate từng hàng, insert vào MongoDB, báo kết quả
    """
    if request.method == 'POST':
        csv_file = request.FILES.get('csv_file')
        if not csv_file:
            messages.error(request, 'Vui lòng chọn file CSV.')
            return redirect('patients:import_csv')

        if not csv_file.name.endswith('.csv'):
            messages.error(request, 'File phải có định dạng .csv')
            return redirect('patients:import_csv')

        if csv_file.size > 5 * 1024 * 1024:  # 5 MB
            messages.error(request, 'File quá lớn (tối đa 5 MB).')
            return redirect('patients:import_csv')

        # Đọc file
        try:
            raw = csv_file.read()
            # Thử decode UTF-8-sig trước (Excel export), rồi UTF-8, rồi cp1252
            for encoding in ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1'):
                try:
                    text = raw.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                messages.error(request, 'Không đọc được file. Hãy lưu file CSV dưới dạng UTF-8.')
                return redirect('patients:import_csv')
        except Exception as exc:
            messages.error(request, f'Lỗi đọc file: {exc}')
            return redirect('patients:import_csv')

        reader = csv.DictReader(io.StringIO(text))

        # Kiểm tra header
        if not reader.fieldnames:
            messages.error(request, 'File CSV trống hoặc không có header.')
            return redirect('patients:import_csv')

        file_cols = set(c.strip() for c in reader.fieldnames)
        missing_required = CSV_REQUIRED_COLS - file_cols
        if missing_required:
            messages.error(
                request,
                f'File thiếu các cột bắt buộc: {", ".join(sorted(missing_required))}. '
                f'Hãy tải file mẫu để xem đúng format.'
            )
            return redirect('patients:import_csv')

        # Parse từng hàng
        success_docs = []
        row_results = []
        total_rows = 0

        for row_num, row in enumerate(reader, start=2):
            total_rows += 1
            clean_row = {k.strip(): (v.strip() if v else '') for k, v in row.items() if k}
            patient_doc, errors = _parse_csv_row(clean_row, row_num)
            if errors:
                row_results.append({
                    'row': row_num,
                    'name': clean_row.get('patient_name', '—'),
                    'cccd': clean_row.get('cccd', '—'),
                    'status': 'error',
                    'message': '; '.join(errors),
                })
            else:
                success_docs.append((row_num, clean_row, patient_doc))
                row_results.append({
                    'row': row_num,
                    'name': patient_doc['patient_name'],
                    'cccd': patient_doc['cccd'],
                    'status': 'pending',
                    'message': f'LOS dự đoán: {patient_doc["predicted_los"]} ngày',
                })

        if not success_docs:
            context = {
                'row_results': row_results,
                'total_rows': total_rows,
                'success_count': 0,
                'error_count': total_rows,
                'show_results': True,
            }
            return render(request, 'patients/import_csv.html', context)

        # Insert vào MongoDB
        collection = get_collection('patients')
        inserted_count = 0
        for row_num, clean_row, doc in success_docs:
            doc['patient_id'] = _generate_eid()
            doc['created_by'] = request.user.username
            doc['created_at'] = datetime.now()
            doc['updated_at'] = datetime.now()
            try:
                collection.insert_one(doc)
                inserted_count += 1
                # Cập nhật row_results status
                for r in row_results:
                    if r['row'] == row_num and r['status'] == 'pending':
                        r['status'] = 'success'
                        r['eid'] = doc['patient_id']
                        r['message'] = f'EID: {doc["patient_id"]} | LOS: {doc["predicted_los"]} ngày'
                        break
            except Exception as exc:
                for r in row_results:
                    if r['row'] == row_num and r['status'] == 'pending':
                        r['status'] = 'error'
                        r['message'] = f'Lỗi lưu DB: {exc}'
                        break

        error_count = sum(1 for r in row_results if r['status'] == 'error')

        if inserted_count > 0:
            messages.success(
                request,
                f'Import thành công {inserted_count}/{total_rows} bệnh nhân.'
                + (f' ({error_count} hàng lỗi)' if error_count else '')
            )
        else:
            messages.error(request, f'Không import được bệnh nhân nào ({error_count} hàng lỗi).')

        context = {
            'row_results': row_results,
            'total_rows': total_rows,
            'success_count': inserted_count,
            'error_count': error_count,
            'show_results': True,
        }
        return render(request, 'patients/import_csv.html', context)

    return render(request, 'patients/import_csv.html', {'show_results': False})
