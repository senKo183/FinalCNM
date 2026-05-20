# Setup v4 — Giai đoạn 3: DVC Data Versioning

Hướng dẫn khởi động hạ tầng **Giai đoạn 3** — xây dựng trên nền Giai đoạn 1 (MLflow + MinIO) và Giai đoạn 2 (Evidently + SHAP).

---

## 1. Yêu cầu tiên quyết

- Đã hoàn thành **SETUP_V2.md** (MLflow + MinIO + PostgreSQL + Redis đang chạy qua Docker).
- Đã hoàn thành **SETUP_V3.md** (Evidently + SHAP đã cài).
- Python venv đã active.
- Git repo đã có remote (cần cho DVC metadata commit).
- MinIO đang chạy (port 9000) — bucket `dvc-storage` đã được tạo tự động bởi `minio_init`.

---

## 2. Cài thêm Python dependencies

```powershell
pip install -r requirements.txt
```

Packages mới của Giai đoạn 3: `dvc[s3]>=3.0`, `PyYAML>=6.0`, `pyarrow>=14.0`.

Kiểm tra:

```powershell
.\venv\Scripts\python -c "import dvc; print('DVC version:', dvc.__version__)"
.\venv\Scripts\python -c "import pyarrow; print('PyArrow OK')"
```

---

## 3. Khởi động Docker stack

```powershell
docker compose up -d
```

Đảm bảo các service đang chạy:

```powershell
docker compose ps
```

Expected output có `los_minio` và `los_mlflow` đều `healthy`.

---

## 4. Khởi tạo DVC (một lần duy nhất)

### 4.1 Init DVC trong repo

```powershell
# Chạy ở root thư mục project
dvc init
```

Lệnh này tạo thư mục `.dvc/` và file `.dvcignore` trong repo. Commit vào Git:

```powershell
git add .dvc .dvcignore
git commit -m "chore: initialize DVC"
```

### 4.2 Cấu hình MinIO remote

Cách 1 — Qua Django shell (tiện nhất, dùng config từ `.env`):

```powershell
python manage.py shell -c "
from ml_engine.dvc_manager import configure_minio_remote
ok, msg = configure_minio_remote()
print('OK' if ok else 'FAILED', msg)
"
```

Cách 2 — Thủ công qua CLI:

```powershell
dvc remote add -d minio_remote s3://dvc-storage/snapshots
dvc remote modify minio_remote endpointurl http://localhost:9000
dvc remote modify minio_remote access_key_id minio_admin
dvc remote modify minio_remote secret_access_key minio_password
```

Commit config DVC remote:

```powershell
git add .dvc/config
git commit -m "chore: configure DVC MinIO remote"
```

### 4.3 Track dataset files gốc

```powershell
dvc add LengthOfStay.csv
dvc add reference_data.csv
dvc push
```

Commit `.dvc` metadata files:

```powershell
git add LengthOfStay.csv.dvc reference_data.csv.dvc
git commit -m "feat(data): track LengthOfStay.csv and reference_data.csv with DVC"
```

> **Lưu ý:** Từ lúc này `LengthOfStay.csv` và `reference_data.csv` sẽ bị thêm vào `.gitignore` bởi DVC — dữ liệu thực tế lưu trên MinIO, Git chỉ lưu metadata `.dvc`.

---

## 5. Chạy Django + Celery

```powershell
# Terminal 1 — Django
python manage.py runserver

# Terminal 2 — Celery worker
celery -A PredictLOSWeb worker -l info --pool=solo

# Terminal 3 — Celery beat
celery -A PredictLOSWeb beat -l info
```

---

## 6. Trang mới (chỉ admin)

| Đường dẫn | Mô tả |
|---|---|
| `/ml/dvc/` | Dashboard DVC — trạng thái, danh sách snapshots, model versions liên kết DVC |

Truy cập qua **Navbar → DVC Data** (chỉ hiển thị cho admin).

---

## 7. Kiểm chứng DVC Data Versioning

### 7.1 Tạo DVC snapshot tự động (qua retrain)

Khi trigger retrain với buffer có dữ liệu:

1. Vào **Navbar → Mô hình ML → Kích hoạt retrain**.
2. Celery task `trigger_retrain` sẽ:
   - Gọi `take_stream_buffer_snapshot()` trước khi train.
   - Export stream_buffer → file `.parquet` trong `data/dvc_snapshots/`.
   - Chạy `dvc add` + `dvc push` lên MinIO.
   - Log `dvc_snapshot_id` và `dvc_md5` vào MLflow Run tags.
   - Lưu `dvc_snapshot_id` vào `model_versions` collection.
3. Kiểm chứng tại `/ml/dvc/` — tab "Model Versions liên kết DVC".

### 7.2 Xem DVC snapshot trên MLflow

Vào **Navbar → MLflow** → chọn run gần nhất → xem phần **Tags**:

- `dvc_snapshot_id` — ID snapshot tương ứng
- `dvc_md5` — Hash MD5 của snapshot file
- `dvc_num_records` — Số records trong snapshot
- `dvc_pushed` — Đã push lên MinIO chưa

### 7.3 Xác minh snapshot được lưu trên MinIO

1. Vào MinIO Console: [http://localhost:9001](http://localhost:9001)
2. Đăng nhập: `minio_admin` / `minio_password`
3. Vào bucket `dvc-storage` → thư mục `snapshots/`
4. Tìm file `.parquet` tương ứng với snapshot

### 7.4 Tái tạo dataset cụ thể (Reproducibility)

```powershell
# Xem lịch sử DVC snapshots
ls data/dvc_snapshots/

# Lấy DVC snapshot ID từ MLflow hoặc trang /ml/dvc/
# Checkout git commit tại thời điểm train đó (nếu cần)
git log --oneline

# Pull data từ MinIO
dvc pull

# Hoặc pull file cụ thể
dvc pull data/dvc_snapshots/stream_buffer_v5_20260428_120000.parquet
```

---

## 8. Track dataset thủ công (khi cập nhật data)

Khi `LengthOfStay.csv` hoặc `reference_data.csv` được cập nhật, chạy:

```powershell
# Qua Celery task
celery call ml_engine.tasks.run_dataset_dvc_track

# Hoặc qua Django shell
python manage.py shell -c "
from ml_engine.tasks import run_dataset_dvc_track
result = run_dataset_dvc_track()
print(result)
"
```

---

## 9. Cấu hình qua `.env`

```env
# DVC Data Versioning (v2 — Giai đoạn 3)
DVC_REMOTE_NAME=minio_remote
DVC_REMOTE_URL=s3://dvc-storage/snapshots
DVC_SNAPSHOTS_DIR=data/dvc_snapshots
```

---

## 10. Troubleshooting

| Lỗi | Giải pháp |
|---|---|
| `DVC không được cài` | `pip install "dvc[s3]>=3.0"` |
| `DVC chưa init` | `dvc init` ở root project |
| `dvc push thất bại` | Kiểm tra MinIO đang chạy: `docker compose ps` |
| `pyarrow ImportError` | `pip install pyarrow>=14.0` |
| `snapshot_id = None` | DVC chưa init hoặc buffer rỗng — hệ thống vẫn retrain bình thường |
| `dvc pull` không lấy được file | Kiểm tra remote: `dvc remote list` |
| File CSV bị xóa sau `dvc add` | Bình thường — DVC chuyển sang symlink/cache. Chạy `dvc checkout` để restore |

---

## 11. Cấu trúc thư mục sau Giai đoạn 3

```
PredictLOSWeb/
├── .dvc/                          # DVC config và internal cache
│   ├── config                     # Remote config (minio_remote)
│   └── cache/                     # Local DVC cache
├── .dvcignore                     # Gitignore style cho DVC
├── data/
│   └── dvc_snapshots/             # Snapshot parquet files (DVC tracked)
│       ├── stream_buffer_v2_*.parquet
│       ├── stream_buffer_v2_*.parquet.dvc
│       └── ...
├── LengthOfStay.csv.dvc           # DVC metadata cho dataset gốc
├── reference_data.csv.dvc         # DVC metadata cho reference data
├── ml_engine/
│   ├── dvc_manager.py             # DVC helper module (MỚI)
│   ├── trainer.py                 # Tích hợp DVC snapshot (CẬP NHẬT)
│   └── tasks.py                   # run_dataset_dvc_track task (CẬP NHẬT)
└── SETUP_V4.md                    # File này
```

---

## 12. Các giai đoạn kế tiếp (chưa triển khai)

- **Giai đoạn 4:** Feast feature store (Redis Online Store cho inference).
- **Giai đoạn 5:** FastAPI serving + Nginx + GitHub Actions CI/CD.
