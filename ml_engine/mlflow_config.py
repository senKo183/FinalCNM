"""MLflow integration helpers — v2 Giai đoạn 1 Foundation.

Cung cấp hàm khởi tạo tracking URI, experiment, và client để dùng trong
Celery retrain task và predictor. Nếu MLflow server không truy cập được,
các hàm này fail gracefully để hệ thống v1 vẫn hoạt động.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)

CHAMPION_ALIAS = 'champion'
STAGING_ALIAS = 'staging'


def _apply_env():
    """Ensure MLflow/boto3 credentials are visible to subprocesses/clients."""
    os.environ['MLFLOW_TRACKING_URI'] = settings.MLFLOW_TRACKING_URI
    os.environ['MLFLOW_S3_ENDPOINT_URL'] = settings.MLFLOW_S3_ENDPOINT_URL
    os.environ['AWS_ACCESS_KEY_ID'] = settings.MLFLOW_AWS_ACCESS_KEY_ID
    os.environ['AWS_SECRET_ACCESS_KEY'] = settings.MLFLOW_AWS_SECRET_ACCESS_KEY


@lru_cache(maxsize=1)
def get_mlflow():
    """Lazy import mlflow + configure tracking URI & experiment.

    Returns None nếu mlflow không cài được (giữ fallback về v1).
    """
    try:
        import mlflow  # noqa: WPS433
    except ImportError:
        logger.warning('mlflow chưa được cài, bỏ qua tracking.')
        return None

    _apply_env()
    mlflow.set_tracking_uri(settings.MLFLOW_TRACKING_URI)

    try:
        mlflow.set_experiment(settings.MLFLOW_EXPERIMENT_NAME)
    except Exception as exc:  # noqa: BLE001
        logger.warning('Không thiết lập được MLflow experiment: %s', exc)
        return None

    return mlflow


def get_mlflow_client():
    """Return MlflowClient hoặc None nếu MLflow không sẵn sàng."""
    mlflow = get_mlflow()
    if mlflow is None:
        return None
    try:
        from mlflow.tracking import MlflowClient  # noqa: WPS433
        return MlflowClient(tracking_uri=settings.MLFLOW_TRACKING_URI)
    except Exception as exc:  # noqa: BLE001
        logger.warning('Không tạo được MlflowClient: %s', exc)
        return None


def is_mlflow_available() -> bool:
    """Ping MLflow server để xác định trạng thái kết nối."""
    client = get_mlflow_client()
    if client is None:
        return False
    try:
        client.search_experiments(max_results=1)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug('MLflow không khả dụng: %s', exc)
        return False


def get_champion_model_uri(registered_name: Optional[str] = None) -> Optional[str]:
    """Trả về URI của model đang gắn alias @champion.

    Dùng cho predictor. Nếu không có @champion (chưa promote) hoặc MLflow
    không khả dụng → None để caller fallback về file pickle v1.
    """
    client = get_mlflow_client()
    if client is None:
        return None

    name = registered_name or settings.MLFLOW_REGISTERED_MODEL_NAME
    try:
        version = client.get_model_version_by_alias(name, CHAMPION_ALIAS)
        return f'models:/{name}@{CHAMPION_ALIAS}'
    except Exception as exc:  # noqa: BLE001
        logger.info('Chưa có @champion cho model %s: %s', name, exc)
        return None


def promote_to_champion(
    version: str | int,
    registered_name: Optional[str] = None,
) -> bool:
    """Gắn alias @champion cho một version cụ thể. Trả về True nếu thành công."""
    client = get_mlflow_client()
    if client is None:
        return False

    name = registered_name or settings.MLFLOW_REGISTERED_MODEL_NAME
    try:
        client.set_registered_model_alias(name, CHAMPION_ALIAS, str(version))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error('Không promote được %s:%s lên @champion: %s', name, version, exc)
        return False
