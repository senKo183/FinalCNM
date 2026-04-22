# Tiến trình phát triển — PredictLOSWeb

### [25/02/2026] – Khởi tạo project & xây dựng toàn bộ hệ thống
- **Đã làm:**
  - Tạo cấu trúc project Django tại D:\CNM\Final\PredictLOSWeb
  - Cấu hình MongoDB Atlas (pymongo), .env, requirements.txt
  - Module Accounts: Đăng nhập/xuất, JWT, hồ sơ cá nhân, đổi mật khẩu, quản lý người dùng (admin)
  - Module Patients: Tạo hồ sơ nhập viện, danh sách bệnh nhân, chi tiết, xuất viện, hệ thống cảnh báo 3 mức
  - Module ML Engine: Dự đoán LOS (GradientBoosting), retrain tự động/thủ công, lịch sử version, stream buffer
  - Module Dashboard: Thống kê tức thời, biểu đồ Chart.js (LOS comparison, error distribution, model performance)
  - Celery tasks: Quét cảnh báo hàng ngày, kiểm tra điều kiện retrain tự động
  - Templates UI: Bootstrap 5 responsive, modern design
  - Copy model best_los_model.pkl và LengthOfStay.csv
- **File thay đổi:** Toàn bộ project mới (30+ files)
- **Lưu ý:**
  - Đã cài dependencies và chạy migrate thành công
  - Đã tạo tài khoản: admin/admin123 (Quản trị viên), doctor1/doctor123 (Bác sĩ)
  - Sửa lỗi Django template không cho phép biến bắt đầu bằng underscore (_id_str -> id_str)
  - Sửa sklearn warning bằng cách dùng DataFrame thay vì numpy array cho prediction
  - Redis cần chạy cho Celery (hoặc retrain sẽ chạy trực tiếp - synchronous fallback)
  - Tất cả trang đã test thành công: Login, Dashboard, Patient List/Create/Detail, ML Versions, Profile, Manage Users

### [25/02/2026] – Cập nhật theo readme [CẬP NHẬT]: CCCD, EID tự động, rcount tự động
- **Đã làm:**
  - Tự động sinh mã EID (PT-YYYYMMDD-XXXX) — bỏ nhập tay patient_id
  - Thêm trường CCCD, ngày sinh, số điện thoại vào form nhập viện
  - Tra cứu CCCD realtime (AJAX debounce) → tự động tính rcount, hiển thị lịch sử nhập viện
  - Trường rcount chuyển sang read-only, tính tự động từ số hồ sơ đã xuất viện cùng CCCD
  - Thêm API endpoint GET /patients/api/lookup-cccd/?cccd=...
  - Cập nhật detail.html, list.html, discharge.html hiển thị CCCD, EID, ngày sinh, SĐT
  - Tìm kiếm danh sách bệnh nhân hỗ trợ thêm CCCD
- **File thay đổi:** patients/views.py, patients/urls.py, templates/patients/create.html, templates/patients/detail.html, templates/patients/list.html, templates/patients/discharge.html
- **Lưu ý:**
  - Phần ML streaming (buffer, retrain conditions, retrain model) đã đúng theo readme — không cần sửa
  - CCCD là định danh xuyên suốt, EID chỉ đại diện cho 1 đợt điều trị
  - rcount cap ở max 5 (tương thích dataset gốc)

### [02/03/2026] – Thêm validation dữ liệu đầu vào (Lần 4 readme)
- **Đã làm:**
  - Ràng buộc trường `secondarydiagnosisnonicd9`: bắt buộc, chỉ chấp nhận số nguyên 0–10, từ chối giá trị trống/thập phân/âm/chuỗi
  - Frontend real-time validation: hiển thị lỗi ngay dưới ô nhập liệu khi vi phạm, không cần submit form
  - Backend validation: kiểm tra lại phía server, trả về thông báo lỗi cụ thể nếu giá trị không hợp lệ
  - Ràng buộc ngày xuất viện: không cho phép chọn thời điểm ở tương lai (max = now, cập nhật mỗi phút)
  - Ràng buộc ngày xuất viện >= ngày nhập viện (tránh actual_los âm)
  - Frontend: tự động set min/max trên input datetime-local, validate real-time khi thay đổi giá trị
  - Backend: validate discharge_date <= now và discharge_date >= admission_date, trả lỗi nếu vi phạm
- **File thay đổi:** patients/views.py, templates/patients/create.html, templates/patients/discharge.html
- **Lưu ý:**
  - Filter theo trạng thái (admitted/discharged) đã có sẵn từ bản trước — không cần sửa
  - Tính năng Lần 3 (stream buffer, retrain conditions) đã đúng từ bản đầu — không cần sửa

### [02/03/2026] – Cập nhật Lần 5 readme: Chỉnh sửa hồ sơ, validation SĐT, cấu hình retrain
- **Đã làm:**
  - Tính năng chỉnh sửa hồ sơ bệnh nhân (`patient_edit` view):
    - Form edit với 2 nhóm: editable (họ tên, SĐT, ngày sinh, cơ sở, chỉ số, bệnh lý) và read-only (EID, CCCD, ngày nhập viện, rcount, LOS)
    - Tự động re-predict LOS nếu features thay đổi, hiển thị confirm trước khi lưu
    - Chỉ bệnh nhân đang điều trị (admitted) mới được chỉnh sửa
    - Thêm nút "Chỉnh sửa" trên trang chi tiết bệnh nhân
  - Validation số điện thoại Việt Nam:
    - Frontend: pattern `0\d{9}`, real-time validation, thông báo lỗi cụ thể (thiếu số 0 đầu, sai số chữ số, ký tự lạ)
    - Backend: regex `^0\d{9}$`, trả lỗi nếu vi phạm
    - Áp dụng cả form tạo mới và form chỉnh sửa
  - Cột "Còn lại" làm tròn 2 chữ số thập phân (`round(max(remaining, 0), 2)`)
  - Cấu hình ngưỡng retrain từ giao diện admin:
    - Lưu `retrain_threshold` và `retrain_interval_days` trong MongoDB collection `system_config`
    - View + template cho trang Cài đặt Retrain (chỉ admin)
    - Celery tasks đọc config từ MongoDB thay vì Django settings
    - Thêm link "Cài đặt Retrain" trong navbar cho admin
    - Bảng gợi ý ngưỡng theo quy mô dữ liệu
- **File thay đổi:** patients/views.py, patients/urls.py, ml_engine/views.py, ml_engine/urls.py, ml_engine/tasks.py, templates/patients/create.html, templates/patients/detail.html, templates/patients/edit.html (mới), templates/patients/list.html, templates/ml_engine/retrain_settings.html (mới), templates/base.html
- **Lưu ý:**
  - Collection `system_config` tự động tạo document mặc định nếu chưa tồn tại (fallback về settings.py)
  - Chỉnh sửa hồ sơ không cho phép thay đổi CCCD vì ảnh hưởng trực tiếp đến logic rcount

### [02/03/2026] – v2 Giai đoạn 1 Foundation: MLflow + MinIO + PostgreSQL + SGDRegressor
- **Đã làm:**
  - Hạ tầng MLOps cơ bản (readme_los_v2.md — Giai đoạn 1 / Tuần 1-2):
    - `docker-compose.yml` với 5 service: `postgres` (MLflow metadata), `minio` + `minio_init` (S3 artifact store, bucket tự tạo `mlflow-artifacts` & `dvc-storage`), `mlflow` (tracking server + registry, port 5000), `redis` (có password — dùng cho Celery + tương lai Feast online store)
    - `.env` mở rộng với POSTGRES_*, MINIO_*, MLFLOW_*, AWS_*, REDIS_PASSWORD/REDIS_URL, MLFLOW_EXPERIMENT_NAME, MLFLOW_REGISTERED_MODEL_NAME
    - `requirements.txt` thêm `mlflow>=2.16`, `boto3>=1.34`, `psycopg2-binary>=2.9`, `sqlalchemy>=2.0`
    - `settings.py` thêm MLFLOW_TRACKING_URI / S3 endpoint / experiment / registry config, tự set AWS creds vào os.environ để MLflow upload artifact qua boto3
  - Module ML nâng cấp:
    - `ml_engine/mlflow_config.py` (mới): helper lazy-import MLflow, `get_mlflow()`, `get_mlflow_client()`, `is_mlflow_available()`, `get_champion_model_uri()`, `promote_to_champion()` — fail gracefully nếu MLflow chưa sẵn sàng
    - `ml_engine/trainer.py`: thay `GradientBoostingRegressor` bằng `SGDRegressor` bọc trong `Pipeline`, hỗ trợ `partial_fit()` khi đã có version SGD trước đó (incremental learning), fallback `fit()` lần đầu. Mỗi retrain tạo MLflow Run log params (alpha, loss, penalty, learning_rate, train_mode) + metrics (mae/rmse/r2/num_samples/improved_vs_champion) + model artifact qua `mlflow.sklearn.log_model()` lên MinIO, đồng thời register vào Registry tên `los_model`. Khi MAE cải thiện thì tự `set_registered_model_alias(..., 'champion', version)`
    - `ml_engine/predictor.py`: ưu tiên `mlflow.sklearn.load_model('models:/los_model@champion')`, fallback về file .pkl local. Thêm `reload_model()` clear cache. Response `predict_los` có thêm `model_source` để debug
  - Admin UI:
    - `ml_engine/views.py` thêm `mlflow_dashboard_view` (admin-only): ping MLflow, list 10 run gần nhất kèm metrics + train_mode + improved, hiển thị @champion version/run_id, link ngoài `MLFLOW_TRACKING_URI`
    - `ml_engine/urls.py` thêm route `mlflow/`
    - `templates/ml_engine/mlflow_dashboard.html` (mới): hiển thị status, bảng runs, hướng dẫn khởi động Docker khi MLflow chưa chạy
    - `templates/base.html` thêm link "MLflow" trong navbar admin
  - `model_versions` collection mở rộng trường mới: `algorithm`, `train_mode` (full_fit/partial_fit), `mlflow_run_id`, `mlflow_model_uri`, `mlflow_registry_version`
  - `SETUP_V2.md` (mới): hướng dẫn khởi động stack, troubleshooting, lộ trình các giai đoạn tiếp theo
- **File thay đổi:** .env, requirements.txt, docker-compose.yml (mới), PredictLOSWeb/settings.py, ml_engine/mlflow_config.py (mới), ml_engine/trainer.py, ml_engine/predictor.py, ml_engine/views.py, ml_engine/urls.py, templates/ml_engine/mlflow_dashboard.html (mới), templates/base.html, SETUP_V2.md (mới)
- **Lưu ý:**
  - Toàn bộ tích hợp MLflow dùng pattern fail-graceful: nếu Docker stack chưa chạy, trainer vẫn ghi model.pkl local và predictor vẫn serve từ file — hệ thống v1 không bị vỡ
  - Không bỏ manual Z-score normalization ở `_prepare_training_data` / `preprocess_features` để tránh double-scaling → Pipeline không dùng `StandardScaler`. Khi triển khai Feast (Giai đoạn 4) sẽ refactor lại thống nhất
  - `partial_fit` chỉ chạy với version có `algorithm='SGDRegressor'`. Version đầu tiên sau nâng cấp vẫn là `full_fit` vì previous version (v1 GradientBoosting) không tương thích
  - Redis giờ có password → `CELERY_BROKER_URL` đã đổi sang `redis://:redis_password@localhost:6379/0`. Nếu đang dev với Redis local không password, cần sửa `.env` cho khớp
  - Các Giai đoạn 2-5 (Evidently, SHAP, DVC, Feast, FastAPI, CI/CD) chưa triển khai — sẽ làm ở lần sau
