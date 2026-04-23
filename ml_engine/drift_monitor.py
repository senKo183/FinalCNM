"""Evidently AI drift monitoring — v2 Giai đoạn 2.

Phát hiện data drift giữa reference dataset (dữ liệu lịch sử ổn định)
và current dataset (dữ liệu bệnh nhân hiện tại từ stream_buffer/patients đã xuất viện).

API tuân theo Evidently 0.6.x:
- Report + DataDriftPreset + DataQualityPreset
- Fail graceful nếu Evidently chưa cài hoặc reference_data chưa có.
"""
from __future__ import annotations

import io
import json
import logging
import os
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
from django.conf import settings

from PredictLOSWeb.mongodb import get_collection

logger = logging.getLogger(__name__)

FEATURE_COLUMNS = [
    'rcount', 'hematocrit', 'neutrophils', 'sodium', 'glucose',
    'bloodureanitro', 'creatinine', 'bmi', 'pulse', 'respiration',
    'secondarydiagnosisnonicd9',
    'dialysisrenalendstage', 'asthma', 'irondef', 'pneum',
    'substancedependence', 'psychologicaldisordermajor', 'depress',
    'psychother', 'fibrosisandother', 'malnutrition', 'hemo',
]

DRIFT_THRESHOLD_HIGH = 0.5
DRIFT_THRESHOLD_MEDIUM = 0.25


def _load_reference_data() -> Optional[pd.DataFrame]:
    """Load reference_data.csv - fallback về LengthOfStay.csv nếu chưa có."""
    ref_path = getattr(settings, 'REFERENCE_DATA_PATH', None)
    if ref_path and os.path.exists(ref_path):
        df = pd.read_csv(ref_path)
        df['rcount'] = pd.to_numeric(
            df['rcount'].astype(str).str.replace('+', '', regex=False),
            errors='coerce',
        ).fillna(0)
        return df[FEATURE_COLUMNS].dropna()

    csv_path = settings.CSV_DATA_PATH
    if os.path.exists(csv_path):
        df = pd.read_csv(csv_path)
        df['rcount'] = pd.to_numeric(
            df['rcount'].astype(str).str.replace('+', '', regex=False),
            errors='coerce',
        ).fillna(0)
        ref_size = int(len(df) * 0.7)
        return df.iloc[:ref_size][FEATURE_COLUMNS].dropna()

    return None


def _build_current_data() -> Optional[pd.DataFrame]:
    """Lấy dữ liệu hiện tại từ stream_buffer (bệnh nhân đã xuất viện gần đây)."""
    buffer_col = get_collection('stream_buffer')
    docs = list(buffer_col.find({}).sort('created_at', -1).limit(500))

    if len(docs) < 10:
        logger.info('Không đủ dữ liệu current (%d docs) để chạy drift.', len(docs))
        return None

    rows = []
    for doc in docs:
        feats = doc.get('features', {})
        row = {}
        for col in FEATURE_COLUMNS:
            row[col] = feats.get(col, np.nan)
        rows.append(row)

    df = pd.DataFrame(rows).dropna(subset=['hematocrit', 'bmi'])
    if len(df) < 10:
        return None
    return df


def _compute_drift_score(report_dict: dict) -> float:
    """Trích xuất overall drift score từ Evidently report dict."""
    try:
        for metric_result in report_dict.get('metrics', []):
            result = metric_result.get('result', {})
            if 'share_of_drifted_columns' in result:
                return float(result['share_of_drifted_columns'])
    except Exception:
        pass
    return 0.0


def _get_per_feature_drift(report_dict: dict) -> list[dict]:
    """Trích xuất drift score per feature."""
    per_feature = []
    try:
        for metric_result in report_dict.get('metrics', []):
            result = metric_result.get('result', {})
            drift_by_columns = result.get('drift_by_columns', {})
            for col, info in drift_by_columns.items():
                per_feature.append({
                    'feature': col,
                    'drift_detected': info.get('drift_detected', False),
                    'drift_score': round(float(info.get('drift_score', 0)), 4),
                    'stattest': info.get('stattest_name', ''),
                    'threshold': round(float(info.get('threshold', 0)), 4),
                })
        per_feature.sort(key=lambda x: x['drift_score'], reverse=True)
    except Exception as exc:
        logger.warning('Không parse được per-feature drift: %s', exc)
    return per_feature


def run_evidently_report(save_html: bool = True) -> Optional[dict]:
    """Chạy Evidently DataDrift report và lưu kết quả vào MongoDB.

    Returns:
        dict với keys: overall_drift_score, drift_level, per_feature, run_at,
        html_report_available, num_current_samples, num_reference_samples
    hoặc None nếu không đủ dữ liệu / lỗi.
    """
    try:
        from evidently.report import Report
        from evidently.metric_preset import DataDriftPreset
    except ImportError:
        logger.error('evidently chưa được cài. Chạy: pip install evidently')
        return None

    reference_df = _load_reference_data()
    if reference_df is None:
        logger.warning('Không tìm thấy reference data.')
        return None

    current_df = _build_current_data()
    if current_df is None:
        logger.info('Không đủ dữ liệu current để chạy Evidently.')
        return None

    # Chỉ dùng các cột có mặt trong cả hai
    common_cols = [c for c in FEATURE_COLUMNS if c in reference_df.columns and c in current_df.columns]
    reference_df = reference_df[common_cols]
    current_df = current_df[common_cols]

    try:
        report = Report(metrics=[DataDriftPreset()])
        report.run(reference_data=reference_df, current_data=current_df)
        report_dict = report.as_dict()
    except Exception as exc:
        logger.error('Evidently report thất bại: %s', exc)
        return None

    overall_score = _compute_drift_score(report_dict)
    per_feature = _get_per_feature_drift(report_dict)

    if overall_score >= DRIFT_THRESHOLD_HIGH:
        drift_level = 'high'
    elif overall_score >= DRIFT_THRESHOLD_MEDIUM:
        drift_level = 'medium'
    else:
        drift_level = 'low'

    html_content = None
    if save_html:
        try:
            html_buffer = io.StringIO()
            report.save_html(html_buffer)
            html_content = html_buffer.getvalue()
        except Exception as exc:
            logger.warning('Không lưu được HTML report: %s', exc)

    result = {
        'overall_drift_score': round(overall_score, 4),
        'drift_level': drift_level,
        'per_feature': per_feature,
        'run_at': datetime.now(),
        'num_reference_samples': len(reference_df),
        'num_current_samples': len(current_df),
        'html_report': html_content,
        'triggered_retrain': False,
    }

    _save_drift_report(result)

    return result


def _save_drift_report(result: dict) -> None:
    """Lưu tóm tắt drift report vào MongoDB collection drift_reports."""
    try:
        col = get_collection('drift_reports')
        doc = {k: v for k, v in result.items() if k != 'html_report'}
        doc['has_html'] = bool(result.get('html_report'))
        col.insert_one(doc)
    except Exception as exc:
        logger.warning('Không lưu được drift report vào MongoDB: %s', exc)


def get_latest_drift_report() -> Optional[dict]:
    """Lấy drift report mới nhất từ MongoDB."""
    try:
        col = get_collection('drift_reports')
        doc = col.find_one(sort=[('run_at', -1)])
        if doc:
            doc['id_str'] = str(doc['_id'])
        return doc
    except Exception:
        return None


def get_drift_history(limit: int = 10) -> list[dict]:
    """Lấy lịch sử drift reports."""
    try:
        col = get_collection('drift_reports')
        docs = list(col.find({}, {'html_report': 0}).sort('run_at', -1).limit(limit))
        for doc in docs:
            doc['id_str'] = str(doc['_id'])
        return docs
    except Exception:
        return []
