"""DVC Manager — v2 Giai đoạn 3: Data Versioning.

Tích hợp DVC snapshot vào pipeline retrain:
- Trước mỗi lần retrain: export stream_buffer → Parquet, dvc add, dvc push
- Trả về snapshot_id (DVC md5 hash) để liên kết với MLflow Run
- Track file CSV gốc (LengthOfStay.csv, reference_data.csv) với DVC
- Fail graceful: nếu DVC chưa init hoặc lỗi, trả None và log warning

Quy trình khởi tạo một lần (xem SETUP_V4.md):
    dvc init
    python manage.py shell -c "from ml_engine.dvc_manager import configure_minio_remote; print(configure_minio_remote())"
    dvc add LengthOfStay.csv reference_data.csv
    dvc push
    git add LengthOfStay.csv.dvc reference_data.csv.dvc .dvcignore
    git commit -m "chore: track dataset files with DVC"
"""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from django.conf import settings

logger = logging.getLogger(__name__)

DVC_SNAPSHOTS_DIR: Path = settings.DVC_SNAPSHOTS_DIR  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run_dvc(args: list, cwd: Optional[str] = None) -> tuple:
    """Chạy lệnh DVC qua subprocess. Trả về (success: bool, output: str)."""
    cmd = ['dvc'] + args
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd or str(settings.BASE_DIR),
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ},
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, (result.stderr or result.stdout).strip()
    except FileNotFoundError:
        return False, 'DVC không được cài. Chạy: pip install "dvc[s3]>=3.0"'
    except subprocess.TimeoutExpired:
        return False, 'DVC command timeout (120s)'
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _extract_dvc_md5(dvc_file: Path) -> Optional[str]:
    """Đọc md5 hash từ file .dvc (YAML format)."""
    try:
        import yaml  # noqa: WPS433
        with open(dvc_file, encoding='utf-8') as f:
            data = yaml.safe_load(f)
        outs = data.get('outs', [])
        if outs:
            # DVC 3.x dùng 'md5' hoặc 'hash'
            return outs[0].get('md5') or outs[0].get('hash') or ''
    except Exception:  # noqa: BLE001
        pass
    return None


def _compute_file_md5(filepath: str) -> str:
    """Tính md5 của file (fallback khi .dvc file không tồn tại)."""
    h = hashlib.md5()
    try:
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                h.update(chunk)
    except Exception:  # noqa: BLE001
        pass
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_dvc_initialized() -> bool:
    """Kiểm tra DVC đã được khởi tạo trong repo chưa."""
    dvc_dir = Path(settings.BASE_DIR) / '.dvc'
    return dvc_dir.is_dir() and (dvc_dir / 'config').exists()


def configure_minio_remote() -> tuple:
    """Cấu hình MinIO làm DVC remote mặc định.

    Đọc thông tin kết nối từ .env / Django settings.
    Chạy một lần trong quá trình setup (xem SETUP_V4.md).

    Returns:
        (success: bool, message: str)
    """
    if not is_dvc_initialized():
        return False, 'DVC chưa init. Chạy: dvc init'

    endpoint = os.environ.get('MINIO_ENDPOINT', 'localhost:9000')
    bucket = os.environ.get('MINIO_BUCKET_DVC', 'dvc-storage')
    access_key = os.environ.get('AWS_ACCESS_KEY_ID', 'minio_admin')
    secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY', 'minio_password')
    remote_name = getattr(settings, 'DVC_REMOTE_NAME', 'minio_remote')
    remote_url = f's3://{bucket}/snapshots'

    cmds = [
        ['remote', 'add', '-d', '-f', remote_name, remote_url],
        ['remote', 'modify', remote_name, 'endpointurl', f'http://{endpoint}'],
        ['remote', 'modify', remote_name, 'access_key_id', access_key],
        ['remote', 'modify', remote_name, 'secret_access_key', secret_key],
    ]

    for cmd in cmds:
        ok, out = _run_dvc(cmd)
        if not ok:
            return False, f'Lỗi khi chạy "dvc {" ".join(cmd)}": {out}'

    return True, f'DVC remote "{remote_name}" → {remote_url} đã được cấu hình.'


def take_stream_buffer_snapshot(
    buffer_docs: list,
    version_label: str = '',
) -> Optional[dict]:
    """Export stream_buffer → Parquet, chạy dvc add + dvc push.

    Args:
        buffer_docs: Danh sách document từ MongoDB collection stream_buffer.
        version_label: Nhãn tùy chọn (ví dụ số version model).

    Returns:
        dict: {'snapshot_id', 'snapshot_file', 'dvc_md5', 'num_records', 'pushed'}
        None nếu không có dữ liệu hoặc DVC chưa init.
    """
    if not buffer_docs:
        logger.info('DVC snapshot: buffer rỗng, bỏ qua.')
        return None

    if not is_dvc_initialized():
        logger.warning(
            'DVC chưa được khởi tạo. '
            'Chạy "dvc init" rồi cấu hình remote theo SETUP_V4.md.'
        )
        return None

    DVC_SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    label_part = f'_{version_label}' if version_label else ''
    filename = f'stream_buffer{label_part}_{timestamp}.parquet'
    snapshot_path = DVC_SNAPSHOTS_DIR / filename

    # Xây dựng DataFrame từ buffer docs
    rows = []
    for doc in buffer_docs:
        row: dict = {
            'buffer_id': str(doc.get('_id', '')),
            'patient_id': str(doc.get('patient_id', '')),
            'actual_los': float(doc.get('actual_los', 0)),
            'used_for_retrain': bool(doc.get('used_for_retrain', False)),
            'created_at': str(doc.get('created_at', '')),
        }
        features = doc.get('features', {})
        row.update({str(k): v for k, v in features.items()})
        rows.append(row)

    df = pd.DataFrame(rows)
    df.to_parquet(str(snapshot_path), index=False)
    logger.info('DVC snapshot: đã export %d records → %s', len(df), snapshot_path)

    rel_path = str(snapshot_path.relative_to(settings.BASE_DIR))

    # dvc add
    ok_add, out_add = _run_dvc(['add', rel_path])
    if not ok_add:
        logger.warning('dvc add thất bại: %s', out_add)
        fallback_md5 = _compute_file_md5(str(snapshot_path))
        return {
            'snapshot_id': f'local_{fallback_md5[:12]}_{timestamp}',
            'snapshot_file': str(snapshot_path),
            'dvc_md5': fallback_md5,
            'num_records': len(df),
            'pushed': False,
            'error': out_add,
        }

    # Lấy md5 từ .dvc file
    dvc_file = Path(str(snapshot_path) + '.dvc')
    dvc_md5 = _extract_dvc_md5(dvc_file) or _compute_file_md5(str(snapshot_path))

    # dvc push (best-effort — MinIO cần đang chạy)
    ok_push, out_push = _run_dvc(['push', rel_path])
    if not ok_push:
        logger.warning('dvc push thất bại (MinIO có thể chưa chạy): %s', out_push)

    snapshot_id = f'dvc_{dvc_md5[:16]}_{timestamp}' if dvc_md5 else f'snap_{timestamp}'

    logger.info(
        'DVC snapshot hoàn tất: id=%s, pushed=%s', snapshot_id, ok_push
    )
    return {
        'snapshot_id': snapshot_id,
        'snapshot_file': str(snapshot_path),
        'dvc_md5': dvc_md5,
        'num_records': len(df),
        'pushed': ok_push,
    }


def track_dataset_file(filepath: str) -> Optional[dict]:
    """Chạy dvc add trên file dataset (LengthOfStay.csv, reference_data.csv).

    Args:
        filepath: Đường dẫn tuyệt đối đến file cần track.

    Returns:
        dict với snapshot_id và dvc_md5, hoặc None nếu lỗi.
    """
    if not is_dvc_initialized():
        logger.warning('DVC chưa init, bỏ qua track_dataset_file.')
        return None

    if not Path(filepath).exists():
        logger.warning('File không tồn tại: %s', filepath)
        return None

    rel = str(Path(filepath).relative_to(settings.BASE_DIR))
    ok, out = _run_dvc(['add', rel])
    if not ok:
        logger.warning('dvc add %s thất bại: %s', rel, out)
        return None

    dvc_file = Path(filepath + '.dvc')
    dvc_md5 = _extract_dvc_md5(dvc_file) or _compute_file_md5(filepath)
    ok_push, _ = _run_dvc(['push', rel])

    return {
        'snapshot_id': f'csv_{dvc_md5[:16]}',
        'file_path': filepath,
        'dvc_md5': dvc_md5,
        'pushed': ok_push,
    }


def get_dvc_status() -> dict:
    """Trả về trạng thái DVC hiện tại của project."""
    if not is_dvc_initialized():
        return {
            'initialized': False,
            'remote_name': None,
            'remote_url': None,
            'snapshots': [],
            'snapshots_dir': str(DVC_SNAPSHOTS_DIR),
        }

    ok_remote, out_remote = _run_dvc(['remote', 'list'])
    remote_info = out_remote if ok_remote else 'Chưa cấu hình'

    # Liệt kê các snapshot file .dvc gần nhất (10 file)
    snapshots = []
    if DVC_SNAPSHOTS_DIR.exists():
        dvc_files = sorted(DVC_SNAPSHOTS_DIR.glob('*.parquet.dvc'), reverse=True)[:10]
        for dvc_f in dvc_files:
            md5 = _extract_dvc_md5(dvc_f) or ''
            snapshots.append({
                'name': dvc_f.stem,  # filename.parquet (bỏ .dvc)
                'dvc_md5': md5[:16],
            })

    return {
        'initialized': True,
        'remote_info': remote_info,
        'snapshots': snapshots,
        'snapshots_dir': str(DVC_SNAPSHOTS_DIR),
        'num_snapshots': len(snapshots),
    }
