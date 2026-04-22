import os
import joblib
import numpy as np
import pandas as pd
from datetime import datetime
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from django.conf import settings
from PredictLOSWeb.mongodb import get_collection
from .predictor import CONTINUOUS_VARS, ISSUE_COLUMNS, FEATURE_ORDER


def _prepare_training_data():
    """Combine original CSV data with real-world stream buffer data."""
    csv_path = settings.CSV_DATA_PATH
    df = pd.read_csv(csv_path)

    df['rcount'] = (
        df['rcount'].astype(str).str.replace('+', '', regex=False)
    )
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
            features = doc.get('features', {})
            for col in CONTINUOUS_VARS:
                if col in statistics and col in features:
                    m, s = statistics[col]['mean'], statistics[col]['std']
                    if s > 0:
                        features[col] = (features[col] - m) / s
            features['number_of_issues'] = sum(features.get(c, 0) for c in ISSUE_COLUMNS)

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


def retrain_model():
    """Retrain the model and create a new version if improved."""
    X, y, new_samples, buffer_ids = _prepare_training_data()

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    model = GradientBoostingRegressor(n_estimators=200, random_state=42)
    model.fit(X_train, y_train)

    preds = model.predict(X_test)
    mae = mean_absolute_error(y_test, preds)
    rmse = float(np.sqrt(mean_squared_error(y_test, preds)))
    r2 = r2_score(y_test, preds)

    versions = get_collection('model_versions')
    current_active = versions.find_one({'is_active': True})
    current_mae = current_active.get('mae', float('inf')) if current_active else float('inf')

    version_count = versions.count_documents({})
    new_version = f'v{version_count + 1}'

    model_filename = f'model_{new_version}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.pkl'
    model_path = os.path.join(settings.ML_MODELS_DIR, model_filename)
    joblib.dump(model, model_path)

    is_better = mae <= current_mae

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
        'description': f'Retrain với {new_samples} mẫu mới' if new_samples > 0 else 'Retrain thủ công',
    }

    if is_better and current_active:
        versions.update_one(
            {'_id': current_active['_id']},
            {'$set': {'is_active': False}}
        )

    versions.insert_one(version_doc)

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
    }
