"""Feast Feature Store integration — v2 Giai đoạn 4.

Giải quyết training-serving skew bằng cách quản lý features tập trung:
- Patient nhập viện  → push_patient_features() → Redis Online Store
- Inference          → get_online_features(cccd) → Redis (fast)
- Fallback           → caller dùng raw patient_doc nếu Feast không khả dụng

Pattern fail-graceful: mọi hàm đều try/except, trả None/False thay vì crash.
Hệ thống vẫn hoạt động bình thường khi Feast chưa setup hoặc Redis chưa chạy.
"""
from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Optional

import pandas as pd
from django.conf import settings

logger = logging.getLogger(__name__)

# ─── Feature Lists ────────────────────────────────────────────────────────────
DEMOGRAPHICS_FEATURES = [
    "patient_demographics:rcount",
    "patient_demographics:bmi",
    "patient_demographics:pulse",
    "patient_demographics:respiration",
    "patient_demographics:secondarydiagnosisnonicd9",
]

LAB_RESULTS_FEATURES = [
    "patient_lab_results:hematocrit",
    "patient_lab_results:neutrophils",
    "patient_lab_results:sodium",
    "patient_lab_results:glucose",
    "patient_lab_results:bloodureanitro",
    "patient_lab_results:creatinine",
]

COMORBIDITIES_FEATURES = [
    "patient_comorbidities:hemo",
    "patient_comorbidities:dialysisrenalendstage",
    "patient_comorbidities:asthma",
    "patient_comorbidities:irondef",
    "patient_comorbidities:pneum",
    "patient_comorbidities:substancedependence",
    "patient_comorbidities:psychologicaldisordermajor",
    "patient_comorbidities:depress",
    "patient_comorbidities:psychother",
    "patient_comorbidities:fibrosisandother",
    "patient_comorbidities:malnutrition",
]

ALL_FEATURES = DEMOGRAPHICS_FEATURES + LAB_RESULTS_FEATURES + COMORBIDITIES_FEATURES


def is_feast_available() -> bool:
    """Kiểm tra Feast đã được cài và feature store đã apply chưa."""
    try:
        import feast  # noqa: F401
    except ImportError:
        return False

    store_dir = settings.FEAST_STORE_DIR
    registry_path = settings.FEAST_REGISTRY_PATH

    if not os.path.isdir(store_dir):
        return False
    if not os.path.isfile(registry_path):
        return False
    return True


@lru_cache(maxsize=1)
def _get_store():
    """Lazy-load FeatureStore instance. Trả None nếu lỗi."""
    try:
        from feast import FeatureStore  # noqa: WPS433
        store = FeatureStore(repo_path=str(settings.FEAST_STORE_DIR))
        return store
    except Exception as exc:  # noqa: BLE001
        logger.warning('Không khởi tạo được FeatureStore: %s', exc)
        return None


def _refresh_store():
    """Xoá cache để force reload FeatureStore (dùng sau feast apply)."""
    _get_store.cache_clear()


def feast_apply() -> tuple[bool, str]:
    """Chạy `feast apply` từ feature_store directory để đăng ký Feature Views.

    Cần chạy một lần sau khi thay đổi features.py.
    Trả về (success, output_message).
    """
    store_dir = str(settings.FEAST_STORE_DIR)
    if not os.path.isdir(store_dir):
        return False, f'Thư mục {store_dir} không tồn tại.'

    # Dùng đường dẫn đầy đủ đến feast executable (tương thích Windows venv)
    import sys  # noqa: WPS433
    venv_scripts = os.path.join(os.path.dirname(sys.executable))
    feast_exe = os.path.join(venv_scripts, 'feast')
    if os.name == 'nt':
        feast_exe += '.exe'
    if not os.path.isfile(feast_exe):
        feast_exe = 'feast'  # fallback PATH

    try:
        result = subprocess.run(
            [feast_exe, 'apply'],
            cwd=store_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )
        _refresh_store()
        if result.returncode == 0:
            return True, result.stdout or 'feast apply thanh cong.'
        return False, result.stderr or result.stdout or 'feast apply that bai.'
    except FileNotFoundError:
        return False, 'feast CLI chua duoc cai. Chay: pip install "feast[redis]>=0.40"'
    except Exception as exc:  # noqa: BLE001
        return False, f'Loi feast apply: {exc}'


def _build_demographics_df(cccd: str, patient_doc: dict) -> pd.DataFrame:
    return pd.DataFrame([{
        'patient_cccd': cccd,
        'rcount': int(patient_doc.get('rcount', 0)),
        'bmi': float(patient_doc.get('bmi', 0.0)),
        'pulse': float(patient_doc.get('pulse', 0.0)),
        'respiration': float(patient_doc.get('respiration', 0.0)),
        'secondarydiagnosisnonicd9': int(patient_doc.get('secondarydiagnosisnonicd9', 0)),
        'event_timestamp': datetime.now(timezone.utc),
    }])


def _build_lab_results_df(cccd: str, patient_doc: dict) -> pd.DataFrame:
    return pd.DataFrame([{
        'patient_cccd': cccd,
        'hematocrit': float(patient_doc.get('hematocrit', 0.0)),
        'neutrophils': float(patient_doc.get('neutrophils', 0.0)),
        'sodium': float(patient_doc.get('sodium', 0.0)),
        'glucose': float(patient_doc.get('glucose', 0.0)),
        'bloodureanitro': float(patient_doc.get('bloodureanitro', 0.0)),
        'creatinine': float(patient_doc.get('creatinine', 0.0)),
        'event_timestamp': datetime.now(timezone.utc),
    }])


def _build_comorbidities_df(cccd: str, patient_doc: dict) -> pd.DataFrame:
    return pd.DataFrame([{
        'patient_cccd': cccd,
        'hemo': int(patient_doc.get('hemo', 0)),
        'dialysisrenalendstage': int(patient_doc.get('dialysisrenalendstage', 0)),
        'asthma': int(patient_doc.get('asthma', 0)),
        'irondef': int(patient_doc.get('irondef', 0)),
        'pneum': int(patient_doc.get('pneum', 0)),
        'substancedependence': int(patient_doc.get('substancedependence', 0)),
        'psychologicaldisordermajor': int(patient_doc.get('psychologicaldisordermajor', 0)),
        'depress': int(patient_doc.get('depress', 0)),
        'psychother': int(patient_doc.get('psychother', 0)),
        'fibrosisandother': int(patient_doc.get('fibrosisandother', 0)),
        'malnutrition': int(patient_doc.get('malnutrition', 0)),
        'event_timestamp': datetime.now(timezone.utc),
    }])


def push_patient_features(patient_doc: dict) -> bool:
    """Push toàn bộ features của một bệnh nhân lên Feast Online Store (Redis).

    Gọi khi:
    - Bệnh nhân nhập viện (create)
    - Hồ sơ bệnh nhân được cập nhật (edit)
    - Bệnh nhân xuất viện (rcount tăng → push lại)

    Trả True nếu push thành công, False nếu lỗi hoặc Feast chưa sẵn sàng.
    """
    store = _get_store()
    if store is None:
        return False

    cccd = patient_doc.get('cccd')
    if not cccd:
        return False

    try:
        store.push('push_demographics', _build_demographics_df(cccd, patient_doc))
        store.push('push_lab_results', _build_lab_results_df(cccd, patient_doc))
        store.push('push_comorbidities', _build_comorbidities_df(cccd, patient_doc))
        logger.debug('Đã push Feast features cho CCCD %s', cccd)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning('Feast push thất bại cho CCCD %s: %s', cccd, exc)
        return False


def get_online_features(cccd: str) -> Optional[dict[str, Any]]:
    """Lấy features từ Feast Online Store (Redis) cho một CCCD.

    Trả về dict với tên field ngắn (không có tên feature view),
    hoặc None nếu Feast không khả dụng / không tìm thấy entity.
    """
    store = _get_store()
    if store is None:
        return None

    try:
        response = store.get_online_features(
            features=ALL_FEATURES,
            entity_rows=[{'patient_cccd': cccd}],
        ).to_dict()

        if not response or 'patient_cccd' not in response:
            return None

        # Kiểm tra ít nhất 1 giá trị không None
        has_data = any(
            v[0] is not None
            for k, v in response.items()
            if k != 'patient_cccd'
        )
        if not has_data:
            return None

        # Trả về dict phẳng: {'rcount': 2, 'bmi': 25.3, ...}
        return {
            k: v[0]
            for k, v in response.items()
            if k != 'patient_cccd' and v and v[0] is not None
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug('Feast get_online_features lỗi cho %s: %s', cccd, exc)
        return None


def materialize_from_parquet(snapshot_path: str) -> tuple[bool, str]:
    """Materialize features từ một parquet snapshot vào Online Store.

    Dùng khi có snapshot DVC mới — ghi trực tiếp vào Redis thay vì
    chờ feast materialize-incremental (phù hợp cho môi trường dev).
    """
    store = _get_store()
    if store is None:
        return False, 'FeatureStore chưa sẵn sàng.'

    try:
        df = pd.read_parquet(snapshot_path)
        if 'event_timestamp' not in df.columns:
            df['event_timestamp'] = datetime.now(timezone.utc)

        pushed = 0
        for _, row in df.iterrows():
            cccd = row.get('patient_cccd') or row.get('cccd')
            if not cccd:
                continue
            patient_dict = row.to_dict()
            patient_dict['cccd'] = cccd
            if push_patient_features(patient_dict):
                pushed += 1

        return True, f'Đã materialize {pushed}/{len(df)} records từ {snapshot_path}'
    except Exception as exc:  # noqa: BLE001
        return False, f'Lỗi materialize: {exc}'


def get_feast_status() -> dict:
    """Trả về dict trạng thái Feast cho admin dashboard."""
    installed = False
    applied = False
    redis_ok = False
    feature_views = []
    online_store_info = {}

    try:
        import feast  # noqa: F401
        installed = True
    except ImportError:
        pass

    store = _get_store() if installed else None
    registry_path = settings.FEAST_REGISTRY_PATH
    applied = os.path.isfile(registry_path)

    if store is not None and applied:
        try:
            fvs = store.list_feature_views()
            feature_views = [
                {
                    'name': fv.name,
                    'entities': [e for e in fv.entities],
                    'features': [f.name for f in fv.schema],
                    'ttl_days': fv.ttl.days if fv.ttl else None,
                    'online': fv.online,
                }
                for fv in fvs
            ]
        except Exception as exc:  # noqa: BLE001
            logger.debug('Không list được feature views: %s', exc)

    try:
        import redis as redis_lib  # noqa: WPS433
        redis_url = settings.FEAST_REDIS_URL
        r = redis_lib.from_url(redis_url, socket_connect_timeout=2)
        r.ping()
        redis_ok = True
        info = r.info('keyspace')
        db_key = 'db1'
        keys_count = info.get(db_key, {}).get('keys', 0) if isinstance(info.get(db_key), dict) else 0
        online_store_info = {
            'url': redis_url.split('@')[-1] if '@' in redis_url else redis_url,
            'db': 1,
            'feature_keys': keys_count,
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug('Redis Feast store không kết nối được: %s', exc)

    return {
        'installed': installed,
        'applied': applied,
        'redis_ok': redis_ok,
        'store_dir': str(settings.FEAST_STORE_DIR),
        'registry_path': str(registry_path),
        'feature_views': feature_views,
        'online_store_info': online_store_info,
        'all_features': ALL_FEATURES,
    }
