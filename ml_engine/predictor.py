"""LOS predictor.

v2 Foundation: ưu tiên load model gắn alias @champion từ MLflow Registry.
Nếu MLflow không khả dụng (dev offline, service chưa chạy...) → fallback về
file .pkl local lưu trong model_versions collection (hành vi v1).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import joblib
import pandas as pd
from django.conf import settings

from PredictLOSWeb.mongodb import get_collection

logger = logging.getLogger(__name__)

_model = None
_model_source = None
_statistics = None

CONTINUOUS_VARS = [
    'hematocrit', 'neutrophils', 'sodium', 'glucose',
    'bloodureanitro', 'creatinine', 'bmi', 'pulse', 'respiration',
]

ISSUE_COLUMNS = [
    'hemo', 'dialysisrenalendstage', 'asthma', 'irondef', 'pneum',
    'substancedependence', 'psychologicaldisordermajor', 'depress',
    'psychother', 'fibrosisandother', 'malnutrition',
]

FEATURE_ORDER = [
    'rcount', 'dialysisrenalendstage', 'asthma', 'irondef', 'pneum',
    'substancedependence', 'psychologicaldisordermajor', 'depress',
    'psychother', 'fibrosisandother', 'malnutrition', 'hemo',
    'hematocrit', 'neutrophils', 'sodium', 'glucose', 'bloodureanitro',
    'creatinine', 'bmi', 'pulse', 'respiration',
    'secondarydiagnosisnonicd9', 'number_of_issues',
]


def _load_statistics():
    global _statistics
    if _statistics is not None:
        return _statistics

    csv_path = settings.CSV_DATA_PATH
    if os.path.exists(csv_path):
        df = pd.read_csv(csv_path)
        _statistics = {}
        for col in CONTINUOUS_VARS:
            _statistics[col] = {
                'mean': float(df[col].mean()),
                'std': float(df[col].std()),
            }
    else:
        _statistics = {}
    return _statistics


def _try_load_from_mlflow() -> Optional[tuple]:
    """Trả về (model, version_label, source) nếu load được từ MLflow."""
    try:
        from .mlflow_config import get_champion_model_uri  # noqa: WPS433
    except Exception:  # noqa: BLE001
        return None

    uri = get_champion_model_uri()
    if not uri:
        return None

    try:
        import mlflow.sklearn  # noqa: WPS433
        model = mlflow.sklearn.load_model(uri)
        return model, uri.split('/')[-1] or 'champion', 'mlflow_registry'
    except Exception as exc:  # noqa: BLE001
        logger.warning('Không load được @champion từ MLflow: %s', exc)
        return None


def _get_active_model():
    global _model, _model_source

    mlflow_result = _try_load_from_mlflow()
    if mlflow_result is not None:
        model, label, source = mlflow_result
        _model = model
        _model_source = source
        return _model, f'mlflow:{label}'

    versions = get_collection('model_versions')
    active = versions.find_one({'is_active': True})

    if active and os.path.exists(active.get('model_file_path', '')):
        _model = joblib.load(active['model_file_path'])
        _model_source = 'local_pkl'
        return _model, active.get('version_number', 'v1')

    default_path = settings.ML_MODELS_DIR / 'best_los_model.pkl'
    if os.path.exists(default_path):
        _model = joblib.load(default_path)
        _model_source = 'bootstrap_pkl'

        existing = versions.find_one({'version_number': 'v1'})
        if not existing:
            versions.insert_one({
                'version_number': 'v1',
                'trained_at': None,
                'num_samples': 100000,
                'mae': 0.3237,
                'rmse': 0.4150,
                'r2_score': 0.9686,
                'is_active': True,
                'model_file_path': str(default_path),
                'algorithm': 'GradientBoosting',
                'description': 'Mô hình khởi tạo (v1 GradientBoosting)',
            })

        return _model, 'v1'

    return None, None


def preprocess_features(raw_features):
    stats = _load_statistics()
    processed = dict(raw_features)

    for col in CONTINUOUS_VARS:
        if col in stats and col in processed:
            mean = stats[col]['mean']
            std = stats[col]['std']
            if std > 0:
                processed[col] = (processed[col] - mean) / std

    number_of_issues = sum(processed.get(col, 0) for col in ISSUE_COLUMNS)
    processed['number_of_issues'] = number_of_issues

    return processed


def predict_los(raw_features):
    model, version = _get_active_model()

    if model is None:
        return {
            'predicted_los': 3.0,
            'model_version': 'fallback',
            'error': 'Không tìm thấy mô hình ML',
        }

    processed = preprocess_features(raw_features)

    feature_vector = [processed.get(f, 0) for f in FEATURE_ORDER]
    X = pd.DataFrame([feature_vector], columns=FEATURE_ORDER)

    prediction = model.predict(X)[0]
    predicted_los = max(round(float(prediction), 2), 0.5)

    return {
        'predicted_los': predicted_los,
        'model_version': version,
        'model_source': _model_source,
    }


def reload_model():
    """Clear cache để request kế tiếp load lại model (dùng khi promote @champion)."""
    global _model, _model_source
    _model = None
    _model_source = None
