# Setup v3 — Giai đoạn 2: Evidently Drift Monitoring + SHAP Explainability

Hướng dẫn khởi động hạ tầng **Giai đoạn 2** — xây dựng trên nền Giai đoạn 1 (MLflow + MinIO + Redis).

---

## 1. Yêu cầu tiên quyết

- Đã hoàn thành Setup v2 (MLflow + MinIO + PostgreSQL + Redis đang chạy qua Docker).
- Python venv đã active.
- File `reference_data.csv` đã có ở root project (tạo tự động từ `LengthOfStay.csv`).

> Nếu chưa có `reference_data.csv`, chạy:
> ```powershell
> .\venv\Scripts\python -c "
> import pandas as pd
> df = pd.read_csv('LengthOfStay.csv')
> df['rcount'] = df['rcount'].astype(str).str.replace('+','',regex=False)
> df['rcount'] = pd.to_numeric(df['rcount'], errors='coerce').fillna(0)
> df.iloc[:int(len(df)*0.7)].to_csv('reference_data.csv', index=False)
> print('Done:', len(df)*0.7, 'rows')
> "
> ```

---

## 2. Cài thêm Python dependencies

```powershell
pip install -r requirements.txt
```

Packages mới của Giai đoạn 2: `evidently>=0.4,<0.7`, `shap>=0.44`, `matplotlib>=3.7`.

Kiểm tra:

```powershell
.\venv\Scripts\python -c "import evidently, shap, matplotlib; print('OK')"
```

---

## 3. Khởi động Docker stack (giống Giai đoạn 1)

```powershell
docker compose up -d
```

Đợi `los_mlflow` healthy (~30–45 giây lần đầu).

---

## 4. Chạy Django + Celery

```powershell
# Terminal 1 — Django
python manage.py runserver

# Terminal 2 — Celery worker
celery -A PredictLOSWeb worker -l info --pool=solo

# Terminal 3 — Celery beat (định kỳ)
celery -A PredictLOSWeb beat -l info
```

---

## 5. Các trang mới (chỉ admin)

| Đường dẫn | Mô tả |
|---|---|
| `/ml/drift/` | Dashboard drift monitoring — Evidently |
| `/ml/drift/api/?format=json` | JSON drift report mới nhất |
| `/ml/drift/api/?format=html` | HTML Evidently report đầy đủ |
| `/ml/explanation/` | SHAP global feature importance |

Truy cập qua **Navbar → Drift Monitor** hoặc **Navbar → SHAP Explain** (chỉ hiển thị cho admin).

---

## 6. Kiểm chứng Evidently Drift Monitoring

### 6.1 Chạy drift report thủ công

Vào **Navbar → Drift Monitor → Chạy ngay**.

Hệ thống sẽ:
1. Đọc `reference_data.csv` (70,000 rows lịch sử).
2. Đọc stream_buffer từ MongoDB (bệnh nhân đã xuất viện).
3. Chạy Evidently `DataDriftPreset` report.
4. Hiển thị `overall_drift_score` và per-feature drift.
5. Lưu vào MongoDB collection `drift_reports`.

> **Lưu ý:** Cần ≥10 mẫu trong stream_buffer để chạy được. Nếu chưa đủ, hãy tạo vài bệnh nhân và xác nhận xuất viện.

### 6.2 Xem API JSON

```powershell
Invoke-WebRequest -Uri "http://localhost:8000/ml/drift/api/?format=json" `
  -Headers @{Cookie="sessionid=<your-session-id>"} | Select-Object -ExpandProperty Content
```

### 6.3 Drift tự động trigger retrain

Khi `overall_drift_score >= 0.5` (HIGH), Celery `check_retrain_conditions` sẽ:
1. Phát hiện `triggered_retrain=False` và `drift_score >= threshold`.
2. Tự gọi `trigger_retrain.delay(...)`.
3. Đánh dấu `triggered_retrain=True` trên drift report.

---

## 7. Kiểm chứng SHAP Explainability

### 7.1 SHAP sau retrain

Sau khi trigger retrain (Navbar → Mô hình ML → Kích hoạt retrain):
1. Trainer tự động tính SHAP global explanation sau evaluation.
2. SHAP bar chart được log vào MLflow artifacts (nếu MLflow chạy).
3. `shap_feature_importance` được lưu vào `model_versions` document.

Kiểm chứng: vào **Navbar → SHAP Explain** → xem bar chart và bảng ranking.

### 7.2 SHAP per bệnh nhân

Mở trang chi tiết bất kỳ bệnh nhân nào. Phần **"Lý do dự đoán"** hiển thị top 5 features ảnh hưởng nhất đến LOS dự đoán của bệnh nhân đó, với hướng (+/−).

> **Lưu ý:** Lần đầu vào trang chi tiết bệnh nhân có thể chậm hơn ~1–2 giây do SHAP tính toán. Các lần sau nếu model không thay đổi sẽ nhanh hơn.

---

## 8. Celery Beat — Lịch tự động

| Task | Lịch | Mô tả |
|---|---|---|
| `daily_alert_scan` | Hàng ngày 06:00 | Cập nhật cảnh báo bệnh nhân |
| `check_retrain_conditions` | Hàng ngày 07:00 | Kiểm tra điều kiện retrain (incl. drift) |
| `weekly-drift-monitoring` | Chủ nhật 02:00 | Chạy Evidently drift report tự động |

---

## 9. Cấu hình ngưỡng drift

Trong file `.env`:

```env
EVIDENTLY_DRIFT_THRESHOLD_HIGH=0.5    # Trigger retrain sớm
EVIDENTLY_DRIFT_THRESHOLD_MEDIUM=0.25 # Cảnh báo theo dõi
```

---

## 10. Troubleshooting

| Lỗi | Giải pháp |
|---|---|
| "Không đủ dữ liệu current" | Cần ≥10 mẫu trong stream_buffer (xuất viện ≥10 bệnh nhân) |
| "SHAP returned None" | Model v1 GradientBoosting không hỗ trợ LinearExplainer → trigger retrain để tạo SGDRegressor |
| `shap` ImportError | `pip install shap>=0.44` |
| `evidently` ImportError | `pip install "evidently>=0.4,<0.7"` |
| SHAP chậm trên trang bệnh nhân | Bình thường lần đầu. Background = 500 rows, tính ~1–2s |
| Drift score = 0.0 luôn | Kiểm tra per-feature drift trong bảng — có thể cần thêm dữ liệu real hơn vào stream_buffer |

---

## 11. Các giai đoạn kế tiếp (chưa triển khai)

- **Giai đoạn 3:** DVC data versioning (liên kết dataset version với MLflow Run).
- **Giai đoạn 4:** Feast feature store (Redis Online Store cho inference).
- **Giai đoạn 5:** FastAPI serving + Nginx + GitHub Actions CI/CD.
