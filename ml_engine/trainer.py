"""LOS retrain — v2 Foundation.

Nâng cấp so với v1:
- LinearRegression/GradientBoosting → SGDRegressor hỗ trợ partial_fit
  (incremental learning, không load toàn bộ DB vào RAM).
- Mỗi lần retrain tạo một MLflow Run log params / metrics / model.
- Model được register vào MLflow Registry; nếu cải thiện sẽ gắn alias
  @champion. Fallback ghi file .pkl local vẫn được giữ để tương thích
  predictor v1.
- model_versions collection lưu thêm mlflow_run_id, mlflow_model_uri.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

import joblib
import numpy as np
import pandas as pd
from django.conf import settings
from sklearn.linear_model import SGDRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

from PredictLOSWeb.mongodb import get_collection

from .mlflow_config import (
    CHAMPION_ALIAS,
    get_mlflow,
    get_mlflow_client,
    promote_to_champion,
)
from .predictor import CONTINUOUS_VARS, FEATURE_ORDER, ISSUE_COLUMNS

logger = logging.getLogger(__name__)


def _prepare_training_data():
    """Gộp dữ liệu CSV gốc với stream_buffer (chưa retrain)."""
    csv_path = settings.CSV_DATA_PATH
    df = pd.read_csv(csv_path)

    df['rcount'] = df['rcount'].astype(str).str.replace('+', '', regex=False)
    df['rcount'] = pd.to_numeric(df['rcount'], errors='coerce').fillna(0)

    statistics = {}
    for col in CONTINUOUS_VARS:
        mean = df[col].mean()
        std = df[col].std()
        statistics[col] = {'mean': float(mean), 'std': float(std)}
        if std > 0:
            df[col] = (df[col] - mean) / std

    for col in ISSUE_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    df['number_of_issues'] = df[ISSUE_COLUMNS].sum(axis=1)
    df['lengthofstay'] = df['lengthofstay'].astype(float)

    X_csv = df[FEATURE_ORDER].values
    y_csv = df['lengthofstay'].values

    buffer = get_collection('stream_buffer')
    buffer_docs = list(buffer.find({'used_for_retrain': False}))

    if buffer_docs:
        X_buffer_list = []
        y_buffer_list = []
        for doc in buffer_docs:
            features = dict(doc.get('features', {}))
            for col in CONTINUOUS_VARS:
                if col in statistics and col in features:
                    m = statistics[col]['mean']
                    s = statistics[col]['std']
                    if s > 0:
                        features[col] = (features[col] - m) / s
            features['number_of_issues'] = sum(
                features.get(c, 0) for c in ISSUE_COLUMNS
            )

            row = [features.get(f, 0) for f in FEATURE_ORDER]
            X_buffer_list.append(row)
            y_buffer_list.append(doc.get('actual_los', 0))

        X_buffer = np.array(X_buffer_list)
        y_buffer = np.array(y_buffer_list)
        X_all = np.vstack([X_csv, X_buffer])
        y_all = np.concatenate([y_csv, y_buffer])
    else:
        X_all = X_csv
        y_all = y_csv

    return X_all, y_all, len(buffer_docs), [str(d['_id']) for d in buffer_docs]


def _load_previous_sgd_model() -> Optional[Pipeline]:
    """Load model SGD từ version active gần nhất để partial_fit tiếp.

    Nếu version hiện tại là GradientBoosting (v1) thì trả None → train from scratch.
    """
    versions = get_collection('model_versions')
    active = versions.find_one({'is_active': True, 'algorithm': 'SGDRegressor'})
    if not active:
        return None

    path = active.get('model_file_path')
    if path and os.path.exists(path):
        try:
            model = joblib.load(path)
            if isinstance(model, Pipeline):
                return model
        except Exception as exc:  # noqa: BLE001
            logger.warning('Không load được previous SGD pipeline: %s', exc)
    return None


def _build_new_pipeline() -> Pipeline:
    """CONTINUOUS_VARS đã được Z-score ở _prepare_training_data/preprocess_features,
    các feature còn lại là binary/rcount nhỏ, không cần thêm scaler."""
    return Pipeline([
        ('sgd', SGDRegressor(
            loss='squared_error',
            penalty='l2',
            alpha=1e-4,
            learning_rate='adaptive',
            eta0=0.01,
            max_iter=1000,
            tol=1e-4,
            random_state=42,
        )),
    ])


def _train_or_update(X_train, y_train, X_buffer_only, y_buffer_only):
    """Return (pipeline, mode) với mode in {'full_fit', 'partial_fit'}."""
    prev = _load_previous_sgd_model()
    if prev is not None and X_buffer_only is not None and len(X_buffer_only) > 0:
        try:
            sgd: SGDRegressor = prev.named_steps['sgd']
            sgd.partial_fit(X_buffer_only, y_buffer_only)
            return prev, 'partial_fit'
        except Exception as exc:  # noqa: BLE001
            logger.warning('partial_fit thất bại, chuyển sang full fit: %s', exc)

    pipeline = _build_new_pipeline()
    pipeline.fit(X_train, y_train)
    return pipeline, 'full_fit'


def retrain_model():
    """Retrain pipeline, log MLflow run, tạo version mới."""
    X, y, new_samples, buffer_ids = _prepare_training_data()

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    X_buffer_only = None
    y_buffer_only = None
    if new_samples > 0:
        X_buffer_only = X[-new_samples:]
        y_buffer_only = y[-new_samples:]

    model, train_mode = _train_or_update(X_train, y_train, X_buffer_only, y_buffer_only)

    preds = model.predict(X_test)
    mae = float(mean_absolute_error(y_test, preds))
    rmse = float(np.sqrt(mean_squared_error(y_test, preds)))
    r2 = float(r2_score(y_test, preds))

    versions = get_collection('model_versions')
    current_active = versions.find_one({'is_active': True})
    current_mae = current_active.get('mae', float('inf')) if current_active else float('inf')

    version_count = versions.count_documents({})
    new_version = f'v{version_count + 1}'

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    model_filename = f'model_{new_version}_{timestamp}.pkl'
    os.makedirs(settings.ML_MODELS_DIR, exist_ok=True)
    model_path = os.path.join(settings.ML_MODELS_DIR, model_filename)
    joblib.dump(model, model_path)

    is_better = mae <= current_mae

    mlflow_run_id, mlflow_model_uri, mlflow_version = _log_to_mlflow(
        model=model,
        new_version=new_version,
        mae=mae,
        rmse=rmse,
        r2=r2,
        num_samples=len(X),
        new_samples=new_samples,
        train_mode=train_mode,
        is_better=is_better,
    )

    version_doc = {
        'version_number': new_version,
        'trained_at': datetime.now(),
        'num_samples': len(X),
        'new_samples': new_samples,
        'mae': round(mae, 6),
        'rmse': round(rmse, 6),
        'r2_score': round(r2, 6),
        'is_active': is_better,
        'model_file_path': model_path,
        'algorithm': 'SGDRegressor',
        'train_mode': train_mode,
        'mlflow_run_id': mlflow_run_id,
        'mlflow_model_uri': mlflow_model_uri,
        'mlflow_registry_version': mlflow_version,
        'description': (
            f'Retrain với {new_samples} mẫu mới' if new_samples > 0 else 'Retrain thủ công'
        ),
    }

    if is_better and current_active:
        versions.update_one(
            {'_id': current_active['_id']},
            {'$set': {'is_active': False}}
        )

    versions.insert_one(version_doc)

    if is_better and mlflow_version is not None:
        promote_to_champion(mlflow_version)

    if buffer_ids:
        from bson import ObjectId
        buffer = get_collection('stream_buffer')
        buffer.update_many(
            {'_id': {'$in': [ObjectId(bid) for bid in buffer_ids]}},
            {'$set': {'used_for_retrain': True}}
        )

    return {
        'version': new_version,
        'mae': mae,
        'rmse': rmse,
        'r2': r2,
        'is_active': is_better,
        'num_samples': len(X),
        'new_samples': new_samples,
        'improved': is_better,
        'train_mode': train_mode,
        'mlflow_run_id': mlflow_run_id,
        'mlflow_model_uri': mlflow_model_uri,
    }


def _log_to_mlflow(
    *,
    model: Pipeline,
    new_version: str,
    mae: float,
    rmse: float,
    r2: float,
    num_samples: int,
    new_samples: int,
    train_mode: str,
    is_better: bool,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Log run + register model. Trả về (run_id, model_uri, registry_version)."""
    mlflow = get_mlflow()
    if mlflow is None:
        return None, None, None

    try:
        sgd: SGDRegressor = model.named_steps['sgd']
        params = {
            'algorithm': 'SGDRegressor',
            'loss': sgd.loss,
            'penalty': sgd.penalty,
            'alpha': sgd.alpha,
            'learning_rate': sgd.learning_rate,
            'eta0': sgd.eta0,
            'max_iter': sgd.max_iter,
            'train_mode': train_mode,
            'internal_version': new_version,
        }

        registered_name = settings.MLFLOW_REGISTERED_MODEL_NAME

        with mlflow.start_run(run_name=f'retrain_{new_version}') as run:
            mlflow.log_params(params)
            mlflow.log_metrics({
                'mae': mae,
                'rmse': rmse,
                'r2': r2,
                'num_samples': num_samples,
                'new_samples': new_samples,
                'improved_vs_champion': 1.0 if is_better else 0.0,
            })
            mlflow.set_tag('train_mode', train_mode)
            mlflow.set_tag('improved', str(is_better))

            import mlflow.sklearn  # noqa: WPS433
            model_info = mlflow.sklearn.log_model(
                sk_model=model,
                artifact_path='model',
                registered_model_name=registered_name,
            )
            run_id = run.info.run_id
            model_uri = f'runs:/{run_id}/model'
            registry_version = getattr(model_info, 'registered_model_version', None)
            if registry_version is None:
                client = get_mlflow_client()
                if client is not None:
                    try:
                        latest = client.get_latest_versions(registered_name)
                        if latest:
                            registry_version = latest[0].version
                    except Exception:  # noqa: BLE001
                        registry_version = None
            return run_id, model_uri, str(registry_version) if registry_version else None
    except Exception as exc:  # noqa: BLE001
        logger.warning('MLflow logging thất bại, bỏ qua: %s', exc)
        return None, None, None
