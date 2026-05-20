# Hệ thống Dự đoán Thời gian Nằm viện (LOS) — Phiên bản MLOps

> **Dự án:** Length of Stay Prediction System v2.0  
> **Kiến trúc:** MLOps Production-Grade  
> **Môn học:** Thiết kế hệ thống Web  

---

## Mục lục

1. [Giới thiệu tổng quan](#1-giới-thiệu-tổng-quan)
2. [Stack công nghệ đầy đủ](#2-stack-công-nghệ-đầy-đủ)
3. [Kiến trúc hệ thống nâng cấp](#3-kiến-trúc-hệ-thống-nâng-cấp)
4. [Các pipeline chi tiết](#4-các-pipeline-chi-tiết)
5. [Chức năng chi tiết (giữ nguyên từ v1)](#5-chức-năng-chi-tiết-giữ-nguyên-từ-v1)
6. [Cơ sở dữ liệu & Lưu trữ](#6-cơ-sở-dữ-liệu--lưu-trữ)
7. [Bảng so sánh hệ thống cũ và mới](#7-bảng-so-sánh-hệ-thống-cũ-và-mới)
8. [Lộ trình triển khai](#8-lộ-trình-triển-khai)

---

## 1. Giới thiệu tổng quan

Hệ thống LOS v2.0 là bản nâng cấp toàn diện từ kiến trúc backend truyền thống (Django + Celery + scikit-learn) lên một hệ thống MLOps sản xuất hoàn chỉnh, tham chiếu trực tiếp từ các pattern đã được kiểm chứng trong dự án **Customer Churn Prediction** (AI Vietnam — 2025).

Trong khi v1.0 đã có cơ chế retrain tự động cơ bản, v2.0 giải quyết các khoảng trống nghiêm trọng: thiếu kiểm soát phiên bản dữ liệu, không có monitoring data drift (đặc biệt nguy hiểm trong bối cảnh y tế), không có model registry chuyên dụng, không có explainability, và không có CI/CD pipeline tự động hóa.

Điểm cốt lõi của v2.0 là mọi thành phần từ dữ liệu đến model đến serving đều được theo dõi, có thể rollback, và có thể tái tạo lại hoàn toàn.

---

## 2. Stack công nghệ đầy đủ

### 2.1 Bảng tổng quan theo phân lớp

| Lớp | Công nghệ | Vai trò | Có trong v1? |
|---|---|---|---|
| **Web Framework** | Django + DRF | REST API, business logic, authentication | ✅ Giữ nguyên |
| **Task Queue** | Celery + Redis | Tác vụ nền, retrain, monitoring | ✅ Mở rộng |
| **Primary Database** | MongoDB | Lưu hồ sơ bệnh nhân, users, config | ✅ Giữ nguyên |
| **ML Framework** | scikit-learn | Linear Regression → SGDRegressor | ✅ Nâng cấp |
| **Experiment Tracking** | MLflow | Log params/metrics/artifacts, so sánh experiments | ❌ Thêm mới |
| **Model Registry** | MLflow Registry | Versioning, staging/champion alias, rollback | ❌ Thêm mới |
| **Artifact Storage** | MinIO S3 | Lưu model file, charts, datasets qua MLflow | ❌ Thêm mới |
| **Metadata Database** | PostgreSQL | Backend metadata cho MLflow Server | ❌ Thêm mới |
| **Data Versioning** | DVC | Theo dõi phiên bản dataset, liên kết với Git | ❌ Thêm mới |
| **Feature Store** | Feast | Quản lý features tập trung, training-serving consistency | ❌ Thêm mới |
| **Online Feature Store** | Redis | Low-latency feature retrieval lúc inference | ❌ Thêm mới (qua Feast) |
| **Drift Monitoring** | Evidently AI | Phát hiện data drift và model performance degradation | ❌ Thêm mới |
| **Explainability** | SHAP | Giải thích feature importance cho từng prediction | ❌ Thêm mới |
| **CI/CD** | GitHub Actions | Tự động hóa train → eval → register → promote | ❌ Thêm mới |
| **Model Serving** | FastAPI | Tách model serving ra khỏi Django | ❌ Thêm mới |
| **Reverse Proxy** | Nginx | Load balancing, routing | ❌ Thêm mới |
| **Containerization** | Docker + Docker Compose | Đóng gói toàn bộ services | ❌ Thêm mới |

### 2.2 Chi tiết từng công nghệ mới

#### MLflow — Experiment Tracking & Model Registry

MLflow đóng vai trò trung tâm của Model Pipeline. Mỗi lần retrain Celery task sẽ tạo một MLflow Run mới, log đầy đủ hyperparameters, metrics (MAE, RMSE, R²), artifacts (file model, SHAP charts) và liên kết với dataset version tương ứng. MLflow Registry quản lý vòng đời model với các alias `@staging` (đang kiểm tra) và `@champion` (đang serving production). Quản trị viên có thể so sánh side-by-side bất kỳ hai lần train nào và rollback về version cũ chỉ bằng một lệnh.

MLflow Server được self-host với MinIO làm artifact store và PostgreSQL làm metadata store — không phụ thuộc cloud provider.

#### MinIO — Object Storage

MinIO là S3-compatible object storage tự host. Trong hệ thống v2.0, MinIO phục vụ hai mục đích: (1) artifact backend của MLflow để lưu model file `.pkl`, confusion matrix, SHAP feature importance chart; (2) remote storage của DVC để lưu các phiên bản dataset. MinIO có thể chạy trên cùng server với các service khác qua Docker Compose.

#### DVC — Data Version Control

DVC theo dõi phiên bản các file dữ liệu song song với Git theo dõi phiên bản code. Trong hệ thống LOS, DVC track ba loại file: dataset gốc dùng để train ban đầu, snapshot của `stream_buffer` tại thời điểm mỗi lần retrain, và `reference_data.csv` dùng cho Evidently monitoring. Mỗi Celery retrain task tự động tạo DVC snapshot và liên kết với MLflow Run ID tương ứng, đảm bảo mọi lần train đều có thể tái tạo lại chính xác.

#### Feast — Feature Store

Feast giải quyết vấn đề training-serving skew — tình trạng khi features dùng lúc train và features lấy lúc inference không đồng nhất. Trong LOS v1, `rcount` được tính thủ công bằng cách query MongoDB mỗi lần inference. Với Feast, các features như `rcount`, `bmi` và các chỉ số xét nghiệm được quản lý tập trung qua Feature Views. Offline Store (Parquet) phục vụ batch training, Online Store (Redis) phục vụ real-time inference qua `get_online_features()`. Feast Entity trong LOS là `patient_cccd` — số CCCD làm khóa định danh chính.

#### Evidently AI — Drift Monitoring

Evidently AI là thành phần quan trọng nhất bổ sung cho bối cảnh y tế. Dữ liệu bệnh nhân thay đổi theo mùa bệnh, theo chính sách bệnh viện, và theo các sự kiện dịch tễ học. Evidently so sánh phân phối của dữ liệu hiện tại với reference dataset và phát hiện khi nào model bắt đầu "lạc hậu" so với thực tế. Drift được tính per-feature: Wasserstein distance cho numerical features (BMI, glucose, creatinine...), Jensen-Shannon distance cho categorical features. Nếu `drift_score` vượt ngưỡng HIGH (>0.5), Celery sẽ trigger retrain sớm bất kể điều kiện số mẫu thông thường.

#### SHAP — Explainability

SHAP (SHapley Additive exPlanations) được tích hợp vào Evaluation step sau mỗi lần retrain. SHAP tính đóng góp của từng feature vào kết quả dự đoán, trả lời câu hỏi "tại sao mô hình dự đoán bệnh nhân này nằm viện 7 ngày?". Kết quả mean absolute SHAP values được log vào MLflow dưới dạng bar chart. Trong y tế, SHAP giúp bác sĩ và quản trị bệnh viện tin tưởng vào hệ thống hơn, đồng thời phát hiện sớm khi model đang phụ thuộc vào feature không hợp lý về mặt y học.

#### GitHub Actions — CI/CD Pipeline

GitHub Actions với self-hosted runner tự động hóa toàn bộ ML workflow khi có code thay đổi trên nhánh `main`. Pipeline gồm các job tuần tự: (1) chạy unit tests, (2) Feast materialize cập nhật Online Store, (3) trigger Celery retrain và chờ kết quả, (4) so sánh metrics với @champion hiện tại, (5) register model mới vào MLflow Registry với alias @staging, (6) promote lên @champion nếu metrics cải thiện. Không cần thao tác thủ công từ người dùng.

#### FastAPI — Model Serving

FastAPI được tách ra làm một service độc lập, chuyên nhận request inference và trả về kết quả. Điều này tách biệt model serving khỏi Django application, cho phép scale hai service độc lập và deploy model mới mà không cần restart Django. FastAPI load model từ MLflow Registry theo alias `@champion`, lấy features từ Feast Online Store, và trả về prediction kèm SHAP explanation cho từng request nếu cần.

#### SGDRegressor — Nâng cấp model

Linear Regression (`sklearn.linear_model.LinearRegression`) được thay bằng `SGDRegressor` hỗ trợ `partial_fit()`. Thay vì Celery load toàn bộ dataset vào RAM để retrain, `partial_fit()` cho phép train incremental — chỉ cần load `stream_buffer` (lượng nhỏ) và cập nhật weights của model hiện tại. Điều này giải quyết vấn đề OOM khi dataset lớn dần, đồng thời giảm thời gian retrain đáng kể.

---

## 3. Kiến trúc hệ thống nâng cấp

```
┌─────────────────────────────────────────────────────────────────┐
│                        DATA PIPELINE                            │
│                                                                 │
│  Raw Data ──► DVC Snapshot ──► Parquet (Offline Store)         │
│                    │                    │                       │
│               MinIO S3            Feast Feature                 │
│               (remote)            Views / Entity               │
│                                        │                       │
│                                   Materialize                   │
│                                        │                       │
│                                  Redis Online Store             │
└────────────────────────────┬────────────────────────────────────┘
                             │ Training Features
┌────────────────────────────▼────────────────────────────────────┐
│                       MODEL PIPELINE                            │
│                                                                 │
│  Celery Task ──► SGDRegressor Training ──► Evaluation          │
│                        │                  (MAE/RMSE/R²/SHAP)   │
│                         └──────────► MLflow Run                │
│                                      (log params+metrics+       │
│                                       artifacts to MinIO)       │
│                                           │                     │
│                                    MLflow Registry              │
│                                    @staging ──► @champion       │
└────────────────────────────┬────────────────────────────────────┘
                             │ @champion model
┌────────────────────────────▼────────────────────────────────────┐
│                      SERVING PIPELINE                           │
│                                                                 │
│  Patient ──► Nginx ──► Django API ──► FastAPI Inference        │
│                                            │       │            │
│                                       Feast    MLflow          │
│                                    get_online  load_model      │
│                                    _features() @champion       │
│                                            │                   │
│                                     Prediction + SHAP          │
│                                            │                   │
│                              Evidently AI Monitoring            │
│                              /monitor/drift endpoint            │
└────────────────────────────┬────────────────────────────────────┘
                             │ drift detected / threshold met
┌────────────────────────────▼────────────────────────────────────┐
│                       CI/CD PIPELINE                            │
│                                                                 │
│  GitHub Push / Scheduled ──► GitHub Actions Runner             │
│                              │                                  │
│                   ┌──────────▼──────────────────┐             │
│                   │ feast materialize             │             │
│                   │ celery trigger retrain        │             │
│                   │ compare metrics vs @champion  │             │
│                   │ register @staging             │             │
│                   │ promote to @champion          │             │
│                   └─────────────────────────────┘             │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Các pipeline chi tiết

### 4.1 Data Pipeline

Data Pipeline chịu trách nhiệm đảm bảo dữ liệu được chuẩn hóa, có phiên bản, và sẵn sàng cho cả training lẫn serving với cùng một định nghĩa feature.

**Bước 1 — Chuẩn bị dữ liệu:** Dataset gốc từ bệnh viện được xử lý qua các bước handle missing values, type casting, feature engineering (tính `rcount` từ lịch sử CCCD), và EDA. Output là file Parquet với `event_timestamp` để Feast biết thời điểm của từng record.

**Bước 2 — DVC Snapshot:** File Parquet được DVC track (`dvc add`) và đẩy lên MinIO remote (`dvc push`). Git commit liên kết code version với data version. Điều này cho phép `dvc pull` về đúng dataset của bất kỳ Git commit nào trong lịch sử.

**Bước 3 — Feast Setup:** Feast được khởi tạo với Entity là `patient_cccd`, Data Source trỏ vào file Parquet, Feature Views định nghĩa các nhóm features (demographics, lab results, comorbidities). `feast apply` đăng ký schema vào Feast Registry.

**Bước 4 — Materialize:** `feast materialize-incremental` đồng bộ dữ liệu mới từ Offline Store vào Redis Online Store. Bước này được chạy tự động bởi GitHub Actions khi có data mới, đảm bảo Online Store luôn cập nhật.

**Kết quả:** Model training gọi `store.get_historical_features()` từ Offline Store; inference gọi `store.get_online_features()` từ Redis — cùng một định nghĩa, không có skew.

### 4.2 Model Pipeline

Model Pipeline bao gồm toàn bộ vòng đời từ training đến registry, được MLflow làm trung tâm.

**Training:** Celery task khởi tạo MLflow Run, log hyperparameters của SGDRegressor, chạy `partial_fit()` với stream buffer data (hoặc full retrain nếu là lần đầu), lưu model artifact vào MinIO qua `mlflow.sklearn.log_model()`.

**Evaluation:** Sau training, Celery chạy evaluation trên test split, log MAE, RMSE, R² vào MLflow Run. SHAP LinearExplainer được chạy để tính feature importance, kết quả được log dưới dạng bar chart artifact. Confusion-style residual plot cũng được lưu.

**Registration:** Nếu MAE mới không tệ hơn @champion hiện tại, model được register vào MLflow Registry với alias `@staging`. Quản trị viên có thể xem chi tiết trên MLflow UI trước khi quyết định promote.

**Promotion:** GitHub Actions (hoặc thủ công qua admin panel) chuyển alias từ `@staging` sang `@champion`. FastAPI service tự động reload model mới từ Registry mà không cần restart.

### 4.3 Serving Pipeline

**Request Flow:** Bệnh nhân nhập viện → nhân viên y tế submit form → Django API validate dữ liệu → gọi FastAPI inference service → FastAPI lấy features từ Feast Online Store (Redis) → load @champion model từ MLflow Registry → trả về LOS prediction + top 3 SHAP features giải thích → Django lưu kết quả vào MongoDB.

**Drift Monitoring:** Sau mỗi ca xuất viện, dữ liệu thực tế được append vào `current_data.csv`. Celery Beat task hàng tuần chạy Evidently Report so sánh `current_data.csv` với `reference_data.csv`. Kết quả drift được expose qua endpoint `/monitor/drift?format=html` (HTML dashboard) và `/monitor/drift?format=json` (JSON để tích hợp alerting). Nếu `overall_drift_score > 0.5`, Celery tự động trigger retrain sớm và thông báo cho quản trị viên.

### 4.4 CI/CD Pipeline

GitHub Actions với self-hosted runner (server bệnh viện hoặc máy tính developer) được kích hoạt theo hai hướng: tự động khi push lên `main`, và theo lịch (cron) hàng tuần để cập nhật Feast.

```yaml
# Tóm tắt workflow
jobs:
  test:         # Unit tests, lint
  feast:        # feast materialize-incremental
  retrain:      # Trigger Celery retrain task, chờ kết quả
  register:     # Đăng ký model mới vào MLflow Registry @staging
  promote:      # Promote @staging → @champion nếu metrics cải thiện
  deploy:       # Restart FastAPI service để load @champion mới
```

---

## 5. Chức năng chi tiết (giữ nguyên từ v1)

Tất cả chức năng từ v1.0 được giữ nguyên hoàn toàn ở tầng nghiệp vụ. Phần này liệt kê tóm tắt để tham chiếu.

**Module Xác thực:** JWT Token, phân quyền Bác sĩ/Y tá và Quản trị viên, đăng nhập/đăng xuất, đổi mật khẩu.

**Module Bệnh nhân:** Tạo hồ sơ nhập viện (auto EID, auto rcount qua CCCD), danh sách với filter trạng thái, cảnh báo 3 mức (bình thường/vàng/đỏ), validation đầy đủ (phone VN, ngày xuất viện không tương lai, secondarydiagnosis integer), chỉnh sửa hồ sơ với re-predict tự động, xác nhận xuất viện.

**Module ML Streaming:** Stream buffer tích lũy dữ liệu sau xuất viện, kích hoạt retrain theo ngưỡng mẫu (cấu hình được) hoặc 7 ngày, đánh dấu buffer đã dùng, lịch sử version model.

**Module Dashboard:** Thống kê tức thời, biểu đồ LOS dự đoán vs thực tế, danh sách cảnh báo ưu tiên.

**Tính năng mới thêm ở tầng nghiệp vụ (v2.0):**

Trang admin có thêm tab **MLflow Dashboard** với link trực tiếp đến MLflow UI để xem experiment history. Tab **Drift Report** hiển thị kết quả Evidently của lần chạy gần nhất. Tab **Model Explanation** cho phép quản trị viên xem SHAP feature importance của @champion hiện tại. Khi bác sĩ xem chi tiết một bệnh nhân, hệ thống hiển thị thêm phần **"Lý do dự đoán"** với top 3 features có ảnh hưởng lớn nhất đến LOS của bệnh nhân đó (dựa trên SHAP individual explanation).

---

## 6. Cơ sở dữ liệu & Lưu trữ

### 6.1 MongoDB (giữ nguyên + mở rộng)

Các collection từ v1 được giữ nguyên: `patients`, `users`, `model_versions`, `stream_buffer`, `system_config`.

Collection `model_versions` được mở rộng thêm trường `mlflow_run_id` và `mlflow_model_uri` để liên kết với MLflow Registry. Collection `stream_buffer` thêm trường `dvc_snapshot_id` để biết record này thuộc data snapshot nào. Collection mới `drift_reports` lưu tóm tắt kết quả Evidently của mỗi lần chạy (full report lưu trong MinIO).

### 6.2 PostgreSQL (mới)

PostgreSQL làm metadata backend cho MLflow Server. Lưu trữ toàn bộ thông tin về experiments, runs, params, metrics, và model registry. PostgreSQL được chọn thay SQLite vì hỗ trợ concurrent access từ nhiều Celery worker.

### 6.3 MinIO (mới)

MinIO lưu trữ hai loại data: (1) MLflow artifacts — model files, charts, SHAP plots, Evidently HTML reports; (2) DVC remote — dataset versions, parquet files, stream buffer snapshots. MinIO expose S3-compatible API, cho phép DVC và MLflow dùng chung cơ sở hạ tầng lưu trữ.

### 6.4 Redis (mở rộng)

Redis từ v1 đã được dùng làm Celery broker. Trong v2.0, Redis đảm nhận thêm vai trò Feast Online Store — lưu features của từng bệnh nhân để inference lấy tức thì qua `get_online_features()` mà không cần query MongoDB.

### 6.5 Feast Registry

Feast Registry (`registry.db`) lưu metadata về Feature Views, Entities, và Data Sources. Trong v2.0, registry được lưu trên MinIO để chia sẻ giữa các môi trường dev/staging/production.

---

## 7. Bảng so sánh hệ thống cũ và mới

### 7.1 So sánh kiến trúc tổng thể

| Tiêu chí | v1.0 (Cũ) | v2.0 (Mới) | Mức độ thay đổi |
|---|---|---|---|
| **Kiến trúc** | Monolith Django | Microservices (Django + FastAPI) | 🔴 Lớn |
| **ML Maturity Level** | Level 1.5 | Level 3 | 🔴 Lớn |
| **Model Serving** | Django view trực tiếp | FastAPI service độc lập | 🔴 Lớn |
| **Containerization** | Không | Docker Compose toàn bộ | 🔴 Lớn |
| **Feature Management** | Query MongoDB thủ công | Feast Feature Store | 🔴 Lớn |
| **Artifact Storage** | Local filesystem | MinIO S3 | 🔴 Lớn |
| **CI/CD** | Không có | GitHub Actions tự động | 🔴 Lớn |
| **Core Business Logic** | Django + MongoDB | Django + MongoDB (giữ nguyên) | 🟢 Không đổi |

### 7.2 So sánh Data Pipeline

| Tiêu chí | v1.0 (Cũ) | v2.0 (Mới) |
|---|---|---|
| **Dataset versioning** | Không có | DVC + MinIO remote |
| **Feature định nghĩa** | Rải rác trong Django views | Tập trung trong Feast Feature Views |
| **Training-Serving consistency** | Không đảm bảo (có thể skew) | Đảm bảo qua cùng Feast API |
| **Feature retrieval lúc inference** | Query MongoDB (chậm, không nhất quán) | Redis Online Store qua `get_online_features()` |
| **rcount computation** | Tính thủ công lúc nhập viện | Pre-computed trong Feast, update sau xuất viện |
| **Reproducibility** | Không thể tái tạo dataset cũ | DVC snapshot liên kết với MLflow Run ID |

### 7.3 So sánh Model Pipeline

| Tiêu chí | v1.0 (Cũ) | v2.0 (Mới) |
|---|---|---|
| **Model algorithm** | LinearRegression (full retrain) | SGDRegressor (incremental `partial_fit`) |
| **Experiment tracking** | Không có | MLflow — log params, metrics, artifacts |
| **Artifact storage** | File `.pkl` trên local disk | MLflow + MinIO |
| **Model versioning** | Flag `is_active` trong MongoDB | MLflow Registry với alias (@staging/@champion) |
| **Staging environment** | Không có | @staging → test → promote @champion |
| **Rollback model** | Phải sửa code/database thủ công | Thay alias trong MLflow (< 1 phút) |
| **Experiment comparison** | Không thể | Side-by-side trên MLflow UI |
| **Explainability** | Không có | SHAP log vào MLflow sau mỗi retrain |
| **RAM khi retrain** | Load toàn bộ DB vào pandas | Chỉ load stream_buffer (nhỏ) qua partial_fit |

### 7.4 So sánh Monitoring & Observability

| Tiêu chí | v1.0 (Cũ) | v2.0 (Mới) |
|---|---|---|
| **Data drift detection** | Không có | Evidently AI — weekly report |
| **Feature drift** | Không có | Per-feature drift score (Wasserstein / J-S distance) |
| **Model performance monitoring** | Xem MAE thủ công trong admin | Evidently classification metrics tự động |
| **Drift-triggered retrain** | Không có | Tự động trigger khi drift_score > 0.5 |
| **Prediction explanation** | Không có | SHAP individual explanation per patient |
| **Model performance history** | Bảng model_versions trong MongoDB | MLflow UI với biểu đồ theo thời gian |
| **Alert khi model suy giảm** | Không có | Celery alert + admin notification |

### 7.5 So sánh DevOps & Vận hành

| Tiêu chí | v1.0 (Cũ) | v2.0 (Mới) |
|---|---|---|
| **Deploy model mới** | Restart Django server thủ công | GitHub Actions tự động, zero-downtime |
| **CI/CD** | Không có | GitHub Actions — test + train + deploy |
| **Phụ thuộc giữa services** | Không rõ ràng | Docker Compose với dependency graph |
| **Scale serving** | Chỉ scale Django | Scale FastAPI độc lập |
| **Monitoring infrastructure** | Không có | MLflow UI + Evidently dashboard |
| **Log tập trung** | Django log file | Structured logging qua MLflow + Celery |

### 7.6 So sánh trải nghiệm người dùng

| Tính năng | v1.0 (Cũ) | v2.0 (Mới) |
|---|---|---|
| **Xem lịch sử model** | Bảng đơn giản trong admin | MLflow UI với biểu đồ đầy đủ |
| **Lý do dự đoán** | Không có | Top 3 SHAP features cho từng bệnh nhân |
| **Cảnh báo drift** | Không có | Dashboard hiển thị drift status + level |
| **Kích hoạt retrain thủ công** | Nút trong admin | Nút trong admin + GitHub Actions |
| **Xem drift report** | Không có | HTML report đầy đủ từ Evidently |
| **So sánh model versions** | Không thể | Side-by-side trong MLflow UI |

### 7.7 So sánh độ rủi ro và an toàn

| Rủi ro | v1.0 (Cũ) | v2.0 (Mới) |
|---|---|---|
| **Model hoạt động kém mà không biết** | ❌ Không phát hiện | ✅ Evidently cảnh báo sớm |
| **Không thể rollback model lỗi** | ❌ Tốn nhiều thời gian | ✅ Đổi alias trong < 1 phút |
| **Training-serving skew** | ❌ Có thể xảy ra | ✅ Feast loại bỏ hoàn toàn |
| **Mất model file** | ❌ Không có backup | ✅ MinIO với redundancy |
| **Data drift trong y tế** | ❌ Không phát hiện | ✅ Weekly automated check |
| **Bác sĩ không tin model** | ❌ Black box | ✅ SHAP giải thích rõ ràng |
| **OOM khi dataset lớn** | ❌ Celery crash | ✅ Incremental learning |

---

## 8. Lộ trình triển khai

### Giai đoạn 1 — Foundation ✅ HOÀN THÀNH (23/04/2026)

Mục tiêu: Có MLflow hoạt động và mọi retrain được log đầy đủ.

**Đã triển khai:** Docker Compose với MLflow Server + MinIO + PostgreSQL + Redis. SGDRegressor thay GradientBoosting với `partial_fit`. MLflow Registry với alias `@champion`. Admin dashboard `/ml/mlflow/`.

Kết quả kiểm chứng: Mọi lần retrain xuất hiện trong MLflow UI với đầy đủ metrics và model artifact.

### Giai đoạn 2 — Monitoring ✅ HOÀN THÀNH (23/04/2026)

Mục tiêu: Có drift monitoring tự động và SHAP explanation.

**Đã triển khai:** Evidently AI drift monitoring (`ml_engine/drift_monitor.py`), SHAP explainability (`ml_engine/shap_explainer.py`), Celery Beat task hàng tuần, API endpoint `/ml/drift/api/`, admin dashboard `/ml/drift/` và `/ml/explanation/`.

Kết quả kiểm chứng: Evidently tạo drift report. SHAP chart log vào MLflow artifacts. Trang chi tiết bệnh nhân hiển thị top 5 SHAP features.

### Giai đoạn 3 — Data Versioning ✅ HOÀN THÀNH (28/04/2026)

Mục tiêu: Mọi dataset có version, reproducible.

**Đã triển khai:**
- `ml_engine/dvc_manager.py`: Module DVC helper — `take_stream_buffer_snapshot()`, `track_dataset_file()`, `configure_minio_remote()`, `get_dvc_status()`.
- `ml_engine/trainer.py`: Tích hợp DVC snapshot trước mỗi lần train — export stream_buffer → Parquet, `dvc add`, `dvc push`, liên kết snapshot ID với MLflow Run tags và `model_versions` document.
- `ml_engine/tasks.py`: Task `run_dataset_dvc_track` để track file dataset khi cập nhật.
- Admin UI `/ml/dvc/`: Dashboard DVC — trạng thái, snapshots, model versions liên kết DVC.
- `requirements.txt`: `dvc[s3]>=3.0`, `PyYAML>=6.0`, `pyarrow>=14.0`.
- `SETUP_V4.md`: Hướng dẫn khởi tạo DVC + MinIO remote + track datasets.

Kết quả kiểm chứng: Mỗi lần retrain tự động tạo DVC snapshot, log vào MLflow. Có thể checkout Git commit bất kỳ và `dvc pull` về đúng dataset tại thời điểm đó.

### Giai đoạn 4 — Feature Store (Tuần 9-11)

Mục tiêu: Feast quản lý features, inference dùng Redis.

Công việc: Khởi tạo Feast repo với Entity `patient_cccd`. Định nghĩa Feature Views cho demographics, lab results, comorbidities. Setup Redis Online Store. Sửa FastAPI inference để dùng `get_online_features()` thay vì query MongoDB. Setup Feast materialize trong GitHub Actions.

Kết quả kiểm chứng: Inference latency giảm (Redis so với MongoDB), training-serving consistency được đảm bảo.

### Giai đoạn 5 — CI/CD & Production (Tuần 12-13)

Mục tiêu: Tự động hóa hoàn toàn, production-ready.

Công việc: Setup GitHub Actions self-hosted runner. Viết workflow YAML cho full pipeline. Tách FastAPI ra service riêng với Docker. Thêm Nginx reverse proxy. Thêm SHAP explanation vào patient detail page.

Kết quả kiểm chứng: Push code lên main tự động trigger toàn bộ pipeline và deploy model mới khi metrics cải thiện.

---

## QUY TẮC BẮT BUỘC

### Cập nhật tiến trình

Sau **mỗi lần hoàn thành một task**, Cursor **phải tự động**:

1. Kiểm tra xem file `PROGRESS.md` ở root project đã tồn tại chưa.
2. Nếu **chưa có** → tạo mới file `PROGRESS.md`.
3. Nếu **đã có** → append (thêm vào cuối), KHÔNG ghi đè.
4. Ghi vào theo format sau:

```
### [DD/MM/YYYY] – Tên task
- **Đã làm:** mô tả ngắn gọn những gì vừa thực hiện
- **File thay đổi:** danh sách file đã tạo/sửa
- **Lưu ý:** vấn đề gặp phải, quyết định quan trọng, việc cần làm tiếp
```

> Quy tắc này áp dụng mọi lúc, kể cả khi người dùng không nhắc.
