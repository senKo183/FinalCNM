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
