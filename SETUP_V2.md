# Setup v2 Foundation (Giai đoạn 1)

Hướng dẫn khởi động hạ tầng MLOps mới: **PostgreSQL + MinIO + MLflow + Redis**.

---

## 1. Chuẩn bị

- Docker Desktop đã cài và đang chạy.
- File `.env` ở root project đã được cập nhật (đã làm tự động).
- Python venv đã active.

## 2. Cài thêm Python dependencies

```powershell
pip install -r requirements.txt
```

Package mới: `mlflow`, `boto3`, `psycopg2-binary`, `sqlalchemy`.

## 3. Khởi động stack

Từ thư mục root project (`d:\CNM\Final\PredictLOSWeb`):

```powershell
docker compose up -d
```

Stack này khởi chạy 5 container:

| Container | Port | Vai trò |
|---|---|---|
| `los_postgres` | 5432 | Metadata backend của MLflow |
| `los_minio` | 9000 (API) / 9001 (Console) | S3-compatible object storage cho MLflow artifacts |
| `los_minio_init` | — | Tạo sẵn bucket `mlflow-artifacts` và `dvc-storage` (one-shot) |
| `los_mlflow` | 5000 | MLflow Tracking Server + Registry |
| `los_redis` | 6379 | Celery broker + (tương lai) Feast online store |

Kiểm tra:

```powershell
docker compose ps
```

Đợi `los_mlflow` chuyển sang trạng thái healthy (khoảng 30–45 giây lần đầu vì cần pip install driver Postgres).

## 4. Truy cập UI

- **MLflow UI:** http://localhost:5000
- **MinIO Console:** http://localhost:9001 (login: `minio_admin` / `minio_password`)

Trong Django, admin có thể vào **Navbar → MLflow** để xem dashboard tổng hợp và link ra MLflow UI gốc.

## 5. Chạy Django + Celery như v1

```powershell
# Terminal 1 — Django
python manage.py runserver

# Terminal 2 — Celery worker
celery -A PredictLOSWeb worker -l info --pool=solo

# Terminal 3 — Celery beat (định kỳ)
celery -A PredictLOSWeb beat -l info
```

Lưu ý: Vì Redis hiện có password, `CELERY_BROKER_URL` trong `.env` dùng
`redis://:redis_password@localhost:6379/0`. Nếu bạn đang chạy Redis local
không password, đổi lại cho khớp.

## 6. Trigger retrain để kiểm chứng

Vào **Navbar → Mô hình ML → Kích hoạt retrain** (cần quyền admin).
Hệ thống sẽ:

1. Tạo model SGDRegressor (hoặc `partial_fit()` nếu đã có version SGD).
2. Log một MLflow Run với params + MAE/RMSE/R².
3. Upload model artifact vào MinIO (bucket `mlflow-artifacts`).
4. Register model vào Registry với tên `los_model`.
5. Nếu MAE ≤ @champion hiện tại → tự gán alias `@champion`.
6. Ghi `mlflow_run_id`, `mlflow_model_uri` vào collection `model_versions`.

Kiểm chứng trên MLflow UI tại `/#/experiments` — sẽ thấy experiment `los_prediction` với run đầu tiên.

## 7. Tắt stack

```powershell
docker compose down          # Giữ lại volume (postgres, minio, redis)
docker compose down -v       # Xoá luôn dữ liệu (cẩn thận — sẽ mất experiments)
```

## 8. Troubleshooting

- **MLflow dashboard báo "chưa sẵn sàng":** kiểm tra `docker compose logs mlflow`. Lần chạy đầu cần ~30s để pip install driver.
- **Celery connect refused Redis:** đảm bảo container `los_redis` đang chạy và password trong `.env` khớp.
- **"No module named 'mlflow'":** chưa `pip install -r requirements.txt` sau khi thêm dependency mới.
- **Model vẫn load từ local pkl:** hệ thống cố ý fallback. Muốn bắt buộc dùng @champion, đợi retrain lần đầu thành công. Sau đó restart Django để clear cache (hoặc gọi `ml_engine.predictor.reload_model()`).

## 9. Các giai đoạn kế tiếp (chưa triển khai)

- **Giai đoạn 2:** Evidently drift monitoring + SHAP explainability.
- **Giai đoạn 3:** DVC data versioning.
- **Giai đoạn 4:** Feast feature store (tận dụng Redis đã sẵn sàng).
- **Giai đoạn 5:** FastAPI serving + Nginx + GitHub Actions CI/CD.
