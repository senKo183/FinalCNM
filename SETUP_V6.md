# Setup v6 — Giai đoạn 5: FastAPI + Nginx + GitHub Actions CI/CD

Hướng dẫn khởi động **Giai đoạn 5** — Production-grade MLOps hoàn chỉnh.  
Xây dựng trên nền Giai đoạn 1-4 (MLflow + Evidently/SHAP + DVC + Feast).

---

## 1. Yêu cầu tiên quyết

- Docker Desktop đang chạy.
- Đã hoàn thành SETUP_V2 → V5 (toàn bộ stack từ giai đoạn 1-4).
- **Django vẫn chạy local** (`python manage.py runserver`) — không cần Dockerize.
- Git remote trỏ về `https://github.com/senKo183/FinalCNM.git`.

---

## 2. Cài thêm dependencies (nếu chưa)

```powershell
pip install -r requirements.txt
```

---

## 3. Khởi động FastAPI + Nginx (Docker)

```powershell
# Build và start FastAPI + Nginx (cùng với stack cũ)
docker compose up -d
```

Hoặc chỉ start 2 service mới:

```powershell
docker compose up -d fastapi nginx
```

**Lần đầu chạy sẽ mất ~3-5 phút** để Docker build image FastAPI (cài requirements).  
Kiểm tra tiến trình:

```powershell
docker compose logs -f fastapi
```

---

## 4. Kiểm tra các service

```powershell
docker compose ps
```

Tất cả service phải ở trạng thái `healthy` hoặc `running`:

| Container | Port | Status |
|---|---|---|
| `los_nginx` | 80 | Reverse proxy tất cả request |
| `los_fastapi` | 8001 | Inference API |
| `los_mlflow` | 5000 | Experiment tracking |
| `los_minio` | 9000/9001 | Artifact storage |
| `los_redis` | 6379 | Celery + Feast |
| `los_postgres` | 5432 | MLflow metadata |

---

## 5. Chạy Django (local, như cũ)

```powershell
# Terminal 1
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
| `/ml/fastapi/` | Dashboard FastAPI — status, model info, reload, links |

Truy cập qua **Navbar → FastAPI** (chỉ hiển thị cho admin).

---

## 7. Kiểm chứng hệ thống đầy đủ

### 7.1 Nginx route đúng

```powershell
# Truy cập Django qua Nginx (port 80 thay vì 8000)
curl http://localhost/accounts/login/

# FastAPI health check qua Nginx
curl http://localhost/api/v2/health

# FastAPI Swagger UI
# Mở trình duyệt: http://localhost/api/v2/docs
```

### 7.2 Inference qua FastAPI REST API

```powershell
curl -X POST http://localhost:8001/api/v2/predict `
  -H "Content-Type: application/json" `
  -d '{
    "rcount": 1,
    "hematocrit": 38.5,
    "neutrophils": 65.0,
    "sodium": 138.0,
    "glucose": 95.0,
    "bloodureanitro": 15.0,
    "creatinine": 0.9,
    "bmi": 24.5,
    "pulse": 72.0,
    "respiration": 18.0,
    "secondarydiagnosisnonicd9": 2,
    "hemo": 0,
    "dialysisrenalendstage": 0,
    "asthma": 0,
    "irondef": 0,
    "pneum": 0,
    "substancedependence": 0,
    "psychologicaldisordermajor": 0,
    "depress": 0,
    "psychother": 0,
    "fibrosisandother": 0,
    "malnutrition": 0
  }'
```

Kết quả mong đợi:
```json
{
  "predicted_los": 3.75,
  "model_version": "mlflow:champion",
  "model_source": "mlflow_registry",
  "feast_used": false,
  "service": "fastapi",
  "timestamp": "2026-05-20T17:00:00.000000"
}
```

### 7.3 Reload model sau khi retrain

```powershell
# Sau khi trigger retrain xong, reload FastAPI lấy @champion mới
curl -X POST http://localhost:8001/api/v2/model/reload
```

Hoặc qua Django admin: **Navbar → FastAPI → "Reload @champion Model"**.

---

## 8. GitHub Actions CI/CD

### 8.1 Setup secrets (cần làm 1 lần)

Vào `https://github.com/senKo183/FinalCNM/settings/secrets/actions` và thêm:

| Secret | Giá trị |
|---|---|
| `MONGODB_URI` | `mongodb+srv://...` |
| `MLFLOW_TRACKING_URI` | `http://<server-ip>:5000` (nếu dùng server riêng) |
| `MLFLOW_S3_ENDPOINT_URL` | `http://<server-ip>:9000` |
| `AWS_ACCESS_KEY_ID` | `minio_admin` |
| `AWS_SECRET_ACCESS_KEY` | `minio_password` |
| `REDIS_URL` | `redis://:redis_password@<server-ip>:6379/0` |
| `FEAST_REDIS_URL` | `redis://:redis_password@<server-ip>:6379/1` |

### 8.2 Setup self-hosted runner

```bash
# Trên máy server/dev, chạy GitHub Actions runner
# Xem hướng dẫn tại:
# https://github.com/senKo183/FinalCNM/settings/actions/runners/new
```

### 8.3 Trigger thủ công

Vào `https://github.com/senKo183/FinalCNM/actions` → chọn workflow **LOS Prediction CI/CD Pipeline** → **Run workflow**.

---

## 9. Cấu trúc thư mục sau Giai đoạn 5

```
PredictLOSWeb/
├── fastapi_service/                  # FastAPI inference service (MỚI)
│   ├── main.py                       # FastAPI app — /predict, /health, /model/info
│   ├── Dockerfile                    # Container config
│   └── requirements.txt             # FastAPI deps
├── nginx/
│   └── nginx.conf                   # Reverse proxy config (MỚI)
├── .github/
│   └── workflows/
│       └── los_pipeline.yml         # CI/CD pipeline (MỚI)
├── docker-compose.yml               # Thêm fastapi + nginx services (CẬP NHẬT)
└── SETUP_V6.md                      # File này
```

---

## 10. Troubleshooting

| Lỗi | Giải pháp |
|---|---|
| `fastapi container unhealthy` | `docker compose logs fastapi` — xem lý do build/start thất bại |
| `nginx: connect() failed` | Django chưa chạy local (`python manage.py runserver`) |
| `FastAPI model_source: none` | MLflow chưa chạy hoặc chưa có @champion; retrain lần đầu để tạo |
| Port 80 bị chiếm | Kiểm tra IIS/Apache: `netstat -ano | findstr :80`; tắt hoặc đổi Nginx sang port 8080 |
| `docker build` lâu | Lần đầu cài requirements (~3-5 phút); các lần sau sẽ dùng cache |

---

## 11. Tổng kết toàn bộ hệ thống MLOps

| Giai đoạn | Stack | Trạng thái |
|---|---|---|
| **1 — Foundation** | MLflow + MinIO + PostgreSQL + SGDRegressor | ✅ Done |
| **2 — Monitoring** | Evidently drift + SHAP explainability | ✅ Done |
| **3 — Data Versioning** | DVC + MinIO remote | ✅ Done |
| **4 — Feature Store** | Feast + Redis Online Store | ✅ Done |
| **5 — CI/CD & Production** | FastAPI + Nginx + GitHub Actions | ✅ Done |
