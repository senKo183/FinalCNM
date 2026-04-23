"""SHAP explainability — v2 Giai đoạn 2.

Tích hợp SHAP LinearExplainer cho SGDRegressor.
Cung cấp:
  - explain_model_global(): mean |SHAP| cho toàn bộ test set → log vào MLflow
  - explain_patient(): SHAP per-instance cho một bệnh nhân → hiển thị trang chi tiết
  - generate_shap_bar_chart(): tạo matplotlib bar chart và trả về bytes

Fail graceful nếu shap chưa cài hoặc model không phải SGDRegressor.
"""
from __future__ import annotations

import base64
import io
import logging
from typing import Optional

import numpy as np
import pandas as pd

from .predictor import CONTINUOUS_VARS, FEATURE_ORDER, ISSUE_COLUMNS

logger = logging.getLogger(__name__)

FEATURE_LABELS = {
    'rcount': 'Số lần tái nhập viện',
    'hematocrit': 'Hematocrit',
    'neutrophils': 'Neutrophils',
    'sodium': 'Sodium',
    'glucose': 'Glucose',
    'bloodureanitro': 'BUN (Ure máu)',
    'creatinine': 'Creatinine',
    'bmi': 'BMI',
    'pulse': 'Nhịp tim',
    'respiration': 'Nhịp thở',
    'secondarydiagnosisnonicd9': 'Chẩn đoán phụ ngoài ICD-9',
    'dialysisrenalendstage': 'Suy thận giai đoạn cuối',
    'asthma': 'Hen suyễn',
    'irondef': 'Thiếu sắt',
    'pneum': 'Viêm phổi',
    'substancedependence': 'Lệ thuộc chất kích thích',
    'psychologicaldisordermajor': 'Rối loạn tâm thần',
    'depress': 'Trầm cảm',
    'psychother': 'Điều trị tâm lý',
    'fibrosisandother': 'Xơ hóa',
    'malnutrition': 'Suy dinh dưỡng',
    'hemo': 'Rối loạn huyết sắc tố',
    'number_of_issues': 'Tổng số bệnh lý',
}


def _get_base_model(model):
    """Trích xuất estimator thực sự từ sklearn Pipeline hoặc trả về nguyên."""
    if hasattr(model, 'named_steps'):
        # Pipeline SGDRegressor
        if 'sgd' in model.named_steps:
            return model.named_steps['sgd'], 'sgd'
        # Lấy step cuối cùng
        last_key = list(model.named_steps.keys())[-1]
        return model.named_steps[last_key], last_key
    return model, 'direct'


def _build_explainer(model, X_background: np.ndarray):
    """Tạo SHAP explainer phù hợp với loại model.

    - SGDRegressor / LinearRegression → shap.LinearExplainer
    - GradientBoostingRegressor / RandomForest → shap.TreeExplainer
    - Fallback → shap.KernelExplainer (chậm, dùng 100 background samples)
    """
    try:
        import shap
    except ImportError:
        return None, None

    base_model, _ = _get_base_model(model)
    model_type = type(base_model).__name__

    try:
        if hasattr(base_model, 'coef_'):
            explainer = shap.LinearExplainer(
                base_model, X_background,
                feature_perturbation='correlation_dependent'
            )
            return explainer, 'linear'

        if hasattr(base_model, 'estimators_') or hasattr(base_model, 'tree_'):
            explainer = shap.TreeExplainer(base_model, X_background[:100])
            return explainer, 'tree'

        # Fallback KernelExplainer — rất chậm
        predict_fn = model.predict if hasattr(model, 'predict') else base_model.predict
        explainer = shap.KernelExplainer(predict_fn, shap.kmeans(X_background, 10))
        return explainer, 'kernel'

    except Exception as exc:
        logger.warning('Không tạo được SHAP explainer (%s): %s', model_type, exc)
        return None, None


def explain_model_global(
    model,
    X_train: np.ndarray,
    X_test: np.ndarray,
    feature_names: list[str] = None,
) -> Optional[dict]:
    """Tính mean |SHAP| trên toàn bộ test set.

    Hỗ trợ SGDRegressor (LinearExplainer) và GradientBoosting (TreeExplainer).

    Returns:
        dict với keys: shap_values (list), feature_importance (list of dicts sorted desc)
        hoặc None nếu lỗi.
    """
    try:
        import shap
    except ImportError:
        logger.warning('shap chưa được cài.')
        return None

    if feature_names is None:
        feature_names = FEATURE_ORDER

    explainer, explainer_type = _build_explainer(model, X_train)
    if explainer is None:
        logger.warning('Không tạo được SHAP explainer.')
        return None

    try:
        X_sample = X_test[:200] if len(X_test) > 200 else X_test
        shap_values = explainer.shap_values(X_sample)
        if isinstance(shap_values, list):
            shap_values = shap_values[0]
        mean_abs_shap = np.abs(shap_values).mean(axis=0)

        # Đảm bảo kích thước khớp với feature_names
        n_features = min(len(feature_names), len(mean_abs_shap))
        feature_importance = [
            {
                'feature': feature_names[i],
                'label': FEATURE_LABELS.get(feature_names[i], feature_names[i]),
                'importance': round(float(mean_abs_shap[i]), 6),
            }
            for i in range(n_features)
        ]
        feature_importance.sort(key=lambda x: x['importance'], reverse=True)

        return {
            'shap_values': shap_values.tolist(),
            'feature_importance': feature_importance,
            'feature_names': feature_names,
            'explainer_type': explainer_type,
        }
    except Exception as exc:
        logger.warning('SHAP global explanation thất bại: %s', exc)
        return None


def explain_patient(
    model,
    X_train: np.ndarray,
    patient_features: dict,
    top_n: int = 5,
) -> Optional[list[dict]]:
    """Tính SHAP explanation cho một bệnh nhân cụ thể.

    Args:
        model: trained sklearn model hoặc Pipeline
        X_train: training data (numpy array) dùng làm background
        patient_features: dict features (đã preprocess)
        top_n: số features quan trọng nhất cần trả về

    Returns:
        list of dicts: [{feature, label, shap_value, direction, importance_rank}]
        sắp xếp theo |shap_value| giảm dần.
        None nếu lỗi.
    """
    try:
        import shap
    except ImportError:
        return None

    explainer, _ = _build_explainer(model, X_train)
    if explainer is None:
        return None

    try:
        row = [patient_features.get(f, 0) for f in FEATURE_ORDER]
        X_instance = np.array([row])

        shap_values = explainer.shap_values(X_instance)
        if isinstance(shap_values, list):
            shap_values = shap_values[0]
        sv_row = shap_values[0]

        explanations = []
        for i, fname in enumerate(FEATURE_ORDER):
            if i >= len(sv_row):
                break
            sv = float(sv_row[i])
            explanations.append({
                'feature': fname,
                'label': FEATURE_LABELS.get(fname, fname),
                'shap_value': round(sv, 4),
                'abs_value': abs(sv),
                'direction': 'tăng' if sv > 0 else 'giảm',
            })

        explanations.sort(key=lambda x: x['abs_value'], reverse=True)
        for i, item in enumerate(explanations):
            item['importance_rank'] = i + 1

        return explanations[:top_n]
    except Exception as exc:
        logger.warning('SHAP patient explanation thất bại: %s', exc)
        return None


def generate_shap_bar_chart(feature_importance: list[dict], top_n: int = 15) -> Optional[bytes]:
    """Tạo matplotlib bar chart từ feature_importance list.

    Returns:
        PNG bytes của chart, hoặc None nếu lỗi.
    """
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    try:
        top = feature_importance[:top_n]
        labels = [item['label'] for item in reversed(top)]
        values = [item['importance'] for item in reversed(top)]

        fig, ax = plt.subplots(figsize=(10, 6))
        colors = ['#e74c3c' if v > 0 else '#3498db' for v in values]
        bars = ax.barh(labels, values, color=colors, edgecolor='white', height=0.6)

        ax.set_xlabel('Mean |SHAP value| (đóng góp vào LOS dự đoán)')
        ax.set_title('Feature Importance — SHAP (Mean |SHAP value|)', fontsize=13, fontweight='bold')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)

        for bar, val in zip(bars, values):
            ax.text(
                bar.get_width() + 0.0005, bar.get_y() + bar.get_height() / 2,
                f'{val:.4f}', va='center', fontsize=9
            )

        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=120, bbox_inches='tight')
        plt.close(fig)
        return buf.getvalue()
    except Exception as exc:
        logger.warning('Tạo SHAP bar chart thất bại: %s', exc)
        return None


def chart_to_base64(chart_bytes: Optional[bytes]) -> Optional[str]:
    """Chuyển PNG bytes sang base64 string để nhúng vào HTML."""
    if chart_bytes is None:
        return None
    return base64.b64encode(chart_bytes).decode('utf-8')
