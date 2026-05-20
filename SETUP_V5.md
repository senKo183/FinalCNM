# Setup v5 — Giai đoạn 4: Feast Feature Store

Hướng dẫn khởi động **Giai đoạn 4** — Feast Feature Store với Redis Online Store.
Xây dựng trên nền Giai đoạn 1-3 (MLflow + Evidently/SHAP + DVC đang chạy qua Docker).

---

## 1. Yêu cầu tiên quyết

- Đã hoàn thành **SETUP_V2.md** (MLflow + MinIO + PostgreSQL + Redis đang chạy).
- Đã hoàn thành **SETUP_V3.md** (Evidently + SHAP đã cài).
- Đã hoàn thành **SETUP_V4.md** (DVC đã init).
- Python venv đã active.
- Redis đang chạy (port 6379, password `redis_password`) — đã có từ docker-compose.

---

## 2. Cài thêm Python dependencies

```powershell
pip install -r requirements.txt
```

Package mới của Giai đoạn 4: `feast[redis]>=0.40`.

Kiểm tra:

```powershell
python -c "import feast; print('Feast version:', feast.__version__)"
```

---

## 3. Khởi động Docker stack (nếu chưa chạy)

```powershell
docker compose up -d
```

Redis phải đang chạy ở port 6379 với password `redis_password`.
Feast dùng Redis **DB 1** (DB 0 dùng cho Celery broker).

---

## 4. Chạy feast apply (một lần duy nhất)

### 4.1 Qua Django Admin Dashboard (khuyến nghị)

1. Mở trình duyệt → Đăng nhập admin → **Navbar → Feast**.
2. Nếu thấy cảnh báo "Chưa chạy feast apply" → click **"Chạy feast apply ngay"**.
3. Đợi thông báo thành công: _"feast apply thành công"_.

### 4.2 Qua CLI (thủ công)

```powershell
cd feature_store
feast apply
```

Output mong đợi:
```
Deploying infrastructure for patient_demographics
Deploying infrastructure for patient_lab_results
Deploying infrastructure for patient_comorbidities
```

Kết quả: File `data/feast_registry/registry.db` được tạo — chứa metadata Feature Views.

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
| `/ml/feast/` | Dashboard Feast — trạng thái cài đặt, Feature Views, Redis Online Store info, actions |

Truy cập qua **Navbar → Feast** (chỉ hiển thị cho admin).

---

## 7. Kiểm chứng Feast Feature Store

### 7.1 Push features tự động khi nhập viện

Khi tạo hồ sơ bệnh nhân mới:

1. Vào **Bệnh nhân → Thêm mới** và điền form.
2. Sau khi submit, Django tự động gọi `push_patient_features()` → push 3 FeatureViews lên Redis DB 1.
3. Kiểm chứng tại `/ml/feast/` — xem Redis online store **feature keys** tăng lên.

### 7.2 Kiểm tra features trên Redis

```powershell
# Kết nối Redis DB 1
python manage.py shell
```

```python
from ml_engine.feast_manager import get_online_features
features = get_online_features("123456789012")  # CCCD của bệnh nhân
print(features)
# {'rcount': 2, 'bmi': 25.3, 'pulse': 78.0, 'hematocrit': 36.5, ...}
```

### 7.3 Inference dùng Feast

`predict_los(raw_features, cccd="123456789012")` sẽ:
1. Gọi `get_online_features("123456789012")` → lấy từ Redis (nhanh).
2. Merge với `raw_features`.
3. Predict với merged features.
4. Kết quả có `"feast_used": True` trong response.

### 7.4 Materialize hàng loạt

Khi Redis khởi động lại (data mất), đồng bộ lại:

```powershell
# Qua admin dashboard
# Vào /ml/feast/ → click "Materialize admitted patients → Redis"

# Qua Django shell
python manage.py shell -c "
from ml_engine.tasks import feast_materialize_admitted
result = feast_materialize_admitted()
print(result)
"
```

---

## 8. Cấu hình `.env`

```env
# Feast Feature Store (v2 — Giai đoạn 4)
FEAST_REDIS_URL=redis://:redis_password@localhost:6379/1
FEAST_STORE_DIR=feature_store
FEAST_REGISTRY_PATH=data/feast_registry/registry.db
```

---

## 9. Cấu trúc thư mục sau Giai đoạn 4

```
PredictLOSWeb/
├── feature_store/                    # Feast repo (MỚI)
│   ├── feature_store.yaml            # Config: provider=local, online=Redis DB1, offline=file
│   └── features.py                   # Entity patient_cccd + 3 FeatureViews + PushSources
├── data/
│   ├── feast_registry/
│   │   └── registry.db               # Feast registry (tạo sau feast apply)
│   └── dvc_snapshots/                # Parquet snapshots (từ Giai đoạn 3)
├── ml_engine/
│   ├── feast_manager.py              # Helper module (MỚI)
│   ├── predictor.py                  # Thêm _enrich_with_feast() (CẬP NHẬT)
│   ├── tasks.py                      # feast_materialize_admitted task (CẬP NHẬT)
│   └── views.py                      # feast_status_view (CẬP NHẬT)
└── SETUP_V5.md                       # File này
```

---

## 10. Troubleshooting

| Lỗi | Giải pháp |
|---|---|
| `feast chưa được cài` | `pip install "feast[redis]>=0.40"` |
| `feast apply thất bại` | Kiểm tra Redis đang chạy: `docker compose ps` |
| `registry.db không tồn tại` | Chạy `feast apply` từ thư mục `feature_store/` |
| `Redis connection refused` | `docker compose up -d redis` |
| `features trả về None` | Chưa push — bệnh nhân tạo trước khi có Feast; dùng "Materialize hàng loạt" |
| `feast_used: False` trong predict` | Feast chưa apply hoặc CCCD chưa có features; hệ thống fallback MongoDB |
| `DB collision với Celery` | Feast dùng Redis DB 1, Celery dùng DB 0 — không xung đột |

---

## 11. Các giai đoạn kế tiếp (chưa triển khai)

- **Giai đoạn 5:** FastAPI serving + Nginx reverse proxy + GitHub Actions CI/CD.
