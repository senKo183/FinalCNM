# Tài liệu mô tả Backend — Hệ thống dự đoán thời gian nằm viện (LOS)

> **Môn học:** Thiết kế hệ thống Web  
> **Công nghệ:** Django · MongoDB · Celery · scikit-learn  
> **Mô hình ML:** Linear Regression (file .pkl)

---

## 📋 Lịch sử cập nhật tài liệu

> Các phần được bổ sung hoặc thay đổi so với bản gốc được đánh dấu bằng ký hiệu **`✏️ [CẬP NHẬT]`** ngay tại tiêu đề tương ứng bên dưới.

| Lần | Ngày cập nhật | Nội dung thay đổi | Vị trí |
|---|---|---|---|
| 1 | 02/03/2026 | Bổ sung thông tin form nhập viện (họ tên, ngày sinh, số điện thoại) | Mục 3.2 |
| 1 | 02/03/2026 | Thêm mới mục tự động sinh mã bệnh nhân EID | Mục 3.2 |
| 1 | 02/03/2026 | Thêm mới mục kiểm tra tái nhập viện và cập nhật rcount tự động | Mục 3.2 |
| 2 | 02/03/2026 | Bổ sung CCCD vào form và thay thế làm định danh chính cho rcount | Mục 3.2 |
| 2 | 02/03/2026 | Cập nhật logic tra cứu realtime theo CCCD, thêm read-only cho rcount | Mục 3.2 |
| 3 | 02/03/2026 | Tách riêng mục Vùng đệm dữ liệu và Điều kiện kích hoạt Retrain | Mục 3.3 |
| 3 | 02/03/2026 | Bổ sung Điều kiện 1 (ngưỡng 10 mẫu) và Điều kiện 2 (7 ngày) cho retrain | Mục 3.3 |
| 3 | 02/03/2026 | Bổ sung ghi chú đánh dấu buffer entries đã dùng sau retrain | Mục 3.3 |
| 4 | 02/03/2026 | Thêm tính năng filter lọc bệnh nhân đang nhập viện / đã xuất viện | Mục 3.2 |
| 4 | 02/03/2026 | Thêm ràng buộc bắt buộc nhập số nguyên cho trường secondarydiagnosisnonicd9 | Mục 3.2 |
| 4 | 02/03/2026 | Thêm ràng buộc ngày xuất viện không được chọn thời điểm ở tương lai | Mục 3.2 |
| 5 | 02/03/2026 | Thêm tính năng chỉnh sửa hồ sơ bệnh nhân | Mục 3.2 |
| 5 | 02/03/2026 | Cập nhật ràng buộc số điện thoại đúng định dạng Việt Nam (10 số, bắt đầu bằng 0) | Mục 3.2 |
| 5 | 02/03/2026 | Cập nhật cột "Còn lại" hiển thị làm tròn 2 chữ số sau dấu phẩy | Mục 3.2 |
| 5 | 02/03/2026 | Thêm tính năng cấu hình ngưỡng số mẫu tự động retrain | Mục 3.3 |

---

## 1. Giới thiệu tổng quan

Hệ thống dự đoán thời gian nằm viện (Length of Stay — LOS) là một ứng dụng web hỗ trợ nhân viên y tế trong việc ước lượng số ngày bệnh nhân cần điều trị nội trú dựa trên các chỉ số lâm sàng và thông tin cá nhân được nhập lúc nhập viện. Kết quả dự đoán được tạo ra bởi mô hình Machine Learning đã được huấn luyện trước, sau đó liên tục được cải thiện thông qua cơ chế học tăng cường từ dữ liệu thực tế (ML Streaming).

Hệ thống phục vụ hai đối tượng chính là bác sĩ/y tá có nhu cầu quản lý và theo dõi tình trạng bệnh nhân, và quản trị viên có quyền giám sát toàn bộ hệ thống cũng như kiểm soát quá trình cập nhật mô hình.

---

## 2. Kiến trúc tổng thể

Hệ thống backend được xây dựng theo mô hình REST API, trong đó Django đóng vai trò xử lý toàn bộ logic nghiệp vụ và cung cấp các endpoint cho frontend gọi đến. MongoDB được chọn làm cơ sở dữ liệu chính nhờ tính linh hoạt trong việc lưu trữ dữ liệu y tế với nhiều trường tùy chọn. Celery kết hợp với Redis đảm nhận việc chạy các tác vụ nền như quét danh sách bệnh nhân hàng ngày và tự động retrain mô hình khi có đủ dữ liệu mới.

Hệ thống gồm bốn module chính hoạt động độc lập nhưng liên kết với nhau:

**Module Xác thực (Authentication)** quản lý đăng nhập, phân quyền và bảo mật truy cập toàn hệ thống.

**Module Bệnh nhân (Patient Management)** xử lý toàn bộ vòng đời hồ sơ bệnh nhân từ lúc nhập viện đến khi xuất viện.

**Module Machine Learning** bao gồm engine dự đoán và cơ chế cập nhật mô hình theo thời gian thực.

**Module Dashboard & Báo cáo** tổng hợp dữ liệu thống kê và cảnh báo cho người dùng.

---

## 3. Chức năng chi tiết

### 3.1 Xác thực và phân quyền

Hệ thống sử dụng xác thực dựa trên JWT Token. Mỗi request từ frontend phải kèm theo token hợp lệ trong header. Token có thời hạn và cần được làm mới định kỳ.

Về phân quyền, hệ thống có hai vai trò chính. **Bác sĩ / Y tá** có thể tạo hồ sơ bệnh nhân, xem danh sách, cập nhật thông tin và xác nhận xuất viện. **Quản trị viên** có thêm quyền xem lịch sử các version mô hình, kích hoạt retrain thủ công và quản lý tài khoản người dùng.

Các chức năng cụ thể bao gồm đăng nhập bằng tên đăng nhập và mật khẩu, đăng xuất (hủy token), xem và chỉnh sửa thông tin cá nhân, và đổi mật khẩu.

---

### 3.2 Quản lý hồ sơ bệnh nhân

Đây là module trung tâm của hệ thống. Mỗi hồ sơ bệnh nhân trải qua hai giai đoạn chính là **đang nằm viện** và **đã xuất viện**.

#### ✏️ [CẬP NHẬT] Tạo hồ sơ nhập viện

Khi bệnh nhân nhập viện, nhân viên y tế điền form với các thông tin sau:

- **Thông tin hành chính:** mã cơ sở y tế, ngày giờ nhập viện, họ tên bệnh nhân, ngày sinh, số CCCD, số điện thoại
- **Thông tin cơ bản:** giới tính, chỉ số BMI
- **Chỉ số xét nghiệm:** hematocrit, hemoglobin, bạch cầu trung tính, natri, glucose, BUN, creatinine, mạch, nhịp thở
- **Bệnh lý kèm theo:** danh sách 11 bệnh nền dạng có/không gồm suy thận giai đoạn cuối, hen suyễn, thiếu sắt, viêm phổi, lệ thuộc chất kích thích, rối loạn tâm thần, trầm cảm, điều trị tâm lý, xơ hóa, suy dinh dưỡng, chẩn đoán phụ ngoài ICD-9

#### ✏️ [CẬP NHẬT] Tự động sinh mã bệnh nhân (EID)

Mã bệnh nhân không do nhân viên y tế nhập tay mà được hệ thống **tự động sinh ra** ngay khi tạo hồ sơ. Mã có định dạng kết hợp giữa tiền tố cố định, ngày tháng và số thứ tự trong ngày, ví dụ `PT-20251001-0042`. Cách sinh này đảm bảo mã luôn duy nhất, dễ đọc và có thể suy ra được ngày nhập viện từ chính mã bệnh nhân.

Lưu ý rằng mỗi lần nhập viện sẽ tạo ra một mã EID mới và độc lập. CCCD mới là định danh xuyên suốt gắn liền với một con người cụ thể, còn EID chỉ đại diện cho một đợt điều trị.

#### ✏️ [CẬP NHẬT] Kiểm tra tái nhập viện và cập nhật rcount tự động

Trường `rcount` thể hiện số lần bệnh nhân đã từng nhập viện trước đó và là một trong các features quan trọng ảnh hưởng đến kết quả dự đoán LOS. Hệ thống sử dụng **số CCCD** làm định danh chính để tra cứu lịch sử vì đây là thông tin duy nhất, chính xác và không thể trùng lặp giữa các bệnh nhân khác nhau, khắc phục hoàn toàn trường hợp trùng họ tên hoặc ngày sinh.

Luồng xử lý diễn ra tự động ngay khi nhân viên y tế nhập số CCCD vào form, không cần chờ đến khi lưu hồ sơ:

Hệ thống tra cứu trong MongoDB theo số CCCD vừa nhập. Nếu không tìm thấy bất kỳ hồ sơ nào khớp, đây là lần nhập viện đầu tiên và `rcount` được tự động đặt là 0. Nếu tìm thấy các hồ sơ cũ có cùng CCCD và đã ở trạng thái xuất viện, hệ thống đếm tổng số hồ sơ đó và gán giá trị tương ứng vào `rcount`. Đồng thời, một thông báo xuất hiện ngay trên form để nhân viên y tế biết bệnh nhân này đã có lịch sử điều trị trước đó, kèm theo nút xem nhanh danh sách các lần nhập viện cũ nếu cần đối chiếu.

Ví dụ minh họa với bệnh nhân có CCCD `001234567890`:

- Lần nhập viện 1 → không tìm thấy CCCD trong hệ thống → `rcount = 0`
- Lần nhập viện 2 → tìm thấy 1 hồ sơ cũ đã xuất viện → `rcount = 1`  
- Lần nhập viện 3 → tìm thấy 2 hồ sơ cũ đã xuất viện → `rcount = 2`

Trường `rcount` trên form sẽ ở trạng thái **chỉ đọc** (read-only), nhân viên y tế không thể chỉnh sửa thủ công để đảm bảo tính nhất quán của dữ liệu đưa vào mô hình dự đoán.

Ngay sau khi lưu, hệ thống tự động gọi mô hình ML để dự đoán LOS và tính ngày dự kiến xuất viện. Kết quả được hiển thị ngay cho nhân viên y tế.

#### ✏️ [CẬP NHẬT] Danh sách và theo dõi bệnh nhân

Trang danh sách hiển thị toàn bộ bệnh nhân kèm trạng thái, số ngày đã nằm viện, số ngày dự đoán còn lại và mức độ cảnh báo. Người dùng có thể lọc theo trạng thái, cơ sở y tế, khoảng thời gian và tìm kiếm theo mã hoặc thông tin bệnh nhân.

**Tính năng lọc theo trạng thái (Filter):** Trang danh sách cung cấp bộ lọc nhanh cho phép nhân viên y tế chọn xem nhóm bệnh nhân nào cần hiển thị. Có ba tùy chọn lọc chính:

- **Tất cả** — hiển thị toàn bộ hồ sơ trong hệ thống, mặc định khi vào trang
- **Đang điều trị** — chỉ hiển thị những bệnh nhân có trạng thái `admitting`, tức đang nằm viện và chưa xuất viện
- **Đã xuất viện** — chỉ hiển thị những bệnh nhân có trạng thái `discharged`, kèm theo thông tin ngày xuất viện thực tế và sai lệch dự đoán

Bộ lọc này hoạt động phía backend, tức là khi người dùng chọn một tùy chọn, frontend gửi request kèm tham số `?status=admitting` hoặc `?status=discharged` lên API, backend thực hiện truy vấn MongoDB với điều kiện tương ứng và trả về đúng tập dữ liệu cần thiết, tránh tải thừa dữ liệu không cần thiết.

Mỗi bệnh nhân được gán một trong ba mức cảnh báo dựa trên ngày dự kiến xuất viện:

- **Bình thường** — còn hơn 2 ngày so với ngày dự kiến
- **Sắp đến hạn** — còn 1 đến 2 ngày, được highlight màu vàng
- **Quá hạn** — đã qua ngày dự kiến mà chưa xuất viện, được highlight màu đỏ

#### ✏️ [CẬP NHẬT] Ràng buộc dữ liệu đầu vào (Validation)

Hệ thống áp dụng các ràng buộc kiểm tra dữ liệu ở cả hai lớp frontend và backend để đảm bảo tính toàn vẹn của dữ liệu đưa vào mô hình dự đoán. Các ràng buộc được áp dụng như sau:

**Trường Chẩn đoán phụ ngoài ICD-9 (`secondarydiagnosisnonicd9`):**
Trường này là bắt buộc, không được phép để trống. Giá trị nhập vào phải là số nguyên (integer) trong khoảng từ 0 đến 10. Hệ thống từ chối mọi giá trị để trống, giá trị thập phân, chuỗi ký tự hoặc số âm. Thông báo lỗi cụ thể sẽ hiển thị ngay dưới ô nhập liệu khi người dùng vi phạm ràng buộc này mà không cần chờ submit form.

**Trường Số điện thoại (`phone_number`):**
Số điện thoại phải đúng định dạng số điện thoại Việt Nam gồm đúng 10 chữ số, bắt đầu bằng số `0`. Hệ thống từ chối mọi giá trị ít hơn hoặc nhiều hơn 10 chữ số, không bắt đầu bằng `0`, hoặc chứa ký tự không phải số. Ví dụ hợp lệ: `0912345678`, `0398765432`. Ví dụ không hợp lệ: `912345678` (thiếu số 0 đầu), `09123456789` (11 số), `0912-345-678` (có dấu gạch ngang).

**Trường Ngày giờ xuất viện (`actual_discharge_date`):**
Khi nhân viên y tế xác nhận xuất viện cho bệnh nhân, trường ngày giờ xuất viện chỉ cho phép chọn các mốc thời gian từ hiện tại trở về quá khứ. Hệ thống tự động khóa không cho chọn bất kỳ thời điểm nào ở tương lai vì ngày xuất viện là sự kiện đã xảy ra trong thực tế, không thể xác nhận trước. Ngoài ra, ngày xuất viện cũng phải lớn hơn hoặc bằng ngày nhập viện của cùng hồ sơ đó, tránh trường hợp nhập liệu sai dẫn đến `actual_los` ra giá trị âm.

**Cột "Còn lại" trên dashboard:**
Giá trị số ngày còn lại đến ngày dự kiến xuất viện được làm tròn và hiển thị tối đa **2 chữ số sau dấu phẩy** trước khi trả về frontend. Việc làm tròn được thực hiện ở tầng serializer của Django REST Framework, đảm bảo frontend luôn nhận giá trị sạch như `2.97` thay vì chuỗi số dài không cần thiết như `2.9699999999999998`.

#### ✏️ [CẬP NHẬT] Xác nhận xuất viện

Khi bệnh nhân xuất viện, nhân viên y tế thực hiện xác nhận bằng cách nhập ngày giờ xuất viện thực tế và tình trạng ra viện (khỏi bệnh, chuyển viện, xin về, tử vong). Hệ thống tự động tính thời gian nằm viện thực tế, so sánh với dự đoán ban đầu và hiển thị mức độ sai lệch. Dữ liệu này sau đó được đưa vào vùng đệm để phục vụ việc cải thiện mô hình.

#### ✏️ [CẬP NHẬT] Chỉnh sửa hồ sơ bệnh nhân

Nhân viên y tế có thể chỉnh sửa thông tin hồ sơ bệnh nhân sau khi đã tạo, phục vụ các trường hợp nhập sai dữ liệu hoặc cập nhật chỉ số xét nghiệm mới. Hệ thống phân chia thành hai nhóm trường có chính sách chỉnh sửa khác nhau.

**Nhóm được phép chỉnh sửa tự do** bao gồm các thông tin hành chính và lâm sàng như họ tên, số điện thoại, ngày sinh, mã cơ sở y tế, chỉ số xét nghiệm, danh sách bệnh lý kèm theo và chẩn đoán phụ ngoài ICD-9.

**Nhóm không được phép chỉnh sửa** bao gồm mã bệnh nhân EID, số CCCD (ảnh hưởng trực tiếp đến logic tính rcount), ngày giờ nhập viện, kết quả dự đoán LOS và version model đã dùng. Những trường này ở trạng thái read-only trên form chỉnh sửa.

Nếu nhân viên y tế chỉnh sửa bất kỳ trường nào thuộc nhóm features đầu vào của mô hình, hệ thống sẽ tự động **chạy lại dự đoán LOS** với bộ features mới và cập nhật lại `predicted_los` cùng `predicted_discharge_date`. Hệ thống sẽ hiển thị thông báo xác nhận trước khi thực hiện để nhân viên y tế biết rằng kết quả dự đoán sẽ thay đổi.

Chỉ bệnh nhân có trạng thái **đang điều trị** mới được phép chỉnh sửa hồ sơ. Bệnh nhân đã xuất viện sẽ bị khóa toàn bộ form chỉnh sửa để bảo toàn tính nhất quán của dữ liệu lịch sử dùng cho retrain.

---

### 3.3 Dự đoán và ML Streaming

#### Cơ chế dự đoán

Mỗi khi tạo hồ sơ bệnh nhân mới, hệ thống lấy các features từ form, đưa qua bước tiền xử lý, rồi nạp vào mô hình hồi quy tuyến tính đang được kích hoạt để lấy kết quả dự đoán. Kết quả được lưu cùng hồ sơ bệnh nhân và ghi nhận version mô hình nào đã được dùng để dự đoán, phục vụ việc so sánh sau này.

#### ✏️ [CẬP NHẬT] Vùng đệm dữ liệu (Stream Buffer)

Sau mỗi lần bệnh nhân xuất viện, dữ liệu gồm features đầu vào và thời gian nằm viện thực tế được thêm vào vùng đệm. Vùng đệm đóng vai trò tích lũy dữ liệu thực tế cho đến khi đủ điều kiện để tiến hành retrain, tránh việc retrain quá thường xuyên gây tốn tài nguyên trong khi lượng dữ liệu bổ sung chưa đủ để tạo ra sự thay đổi có ý nghĩa cho mô hình.

#### ✏️ [CẬP NHẬT] Điều kiện kích hoạt Retrain

Hệ thống không retrain sau mỗi dòng dữ liệu mới mà sẽ tự động kích hoạt quá trình retrain khi thỏa mãn một trong hai điều kiện sau:

**Điều kiện 1 — Đủ ngưỡng số mẫu:** Vùng đệm tích lũy đủ số bản ghi mới theo ngưỡng đã cấu hình kể từ lần retrain gần nhất. Ngưỡng mặc định khi khởi tạo hệ thống là 10 mẫu, tuy nhiên quản trị viên có thể điều chỉnh lại bất kỳ lúc nào thông qua giao diện cài đặt (xem mục Cấu hình ngưỡng retrain bên dưới).

**Điều kiện 2 — Đủ thời gian chờ:** Đã qua 7 ngày kể từ lần retrain cuối mà chưa đủ số mẫu theo ngưỡng. Điều kiện này đảm bảo mô hình vẫn được cập nhật định kỳ ngay cả khi cơ sở y tế có ít bệnh nhân, tránh trường hợp mô hình không bao giờ được cải thiện do không đạt ngưỡng số mẫu.

Khi một trong hai điều kiện trên được thỏa mãn, hệ thống tự động đưa tác vụ retrain vào hàng đợi Celery để xử lý trong nền mà không làm gián đoạn hoạt động bình thường của ứng dụng.

#### ✏️ [CẬP NHẬT] Cấu hình ngưỡng số mẫu tự động retrain

Do lượng dữ liệu tích lũy theo thời gian ngày càng lớn, thời gian mỗi lần retrain sẽ kéo dài hơn. Để linh hoạt điều chỉnh tần suất retrain phù hợp với thực tế vận hành, hệ thống cung cấp tính năng cho phép quản trị viên tự cấu hình ngưỡng số mẫu kích hoạt retrain mà không cần sửa code.

Cấu hình này được lưu trong MongoDB dưới dạng một document cài đặt hệ thống (collection `system_config`), bao gồm các tham số sau:

- **`retrain_threshold`** — số mẫu mới tối thiểu cần có trong buffer để trigger retrain (mặc định: 10)
- **`retrain_interval_days`** — số ngày tối đa chờ đợi trước khi retrain dù chưa đủ mẫu (mặc định: 7)

Quản trị viên có thể thay đổi các giá trị này trực tiếp từ giao diện trang Cài đặt hệ thống mà không cần khởi động lại server. Sau khi lưu, giá trị mới có hiệu lực ngay cho lần kiểm tra điều kiện retrain tiếp theo.

Gợi ý điều chỉnh ngưỡng theo quy mô dữ liệu:

| Tổng số bản ghi trong DB | Ngưỡng đề xuất | Lý do |
|---|---|---|
| Dưới 500 | 10 mẫu | Dữ liệu ít, retrain nhanh, nên cập nhật thường xuyên |
| 500 — 2.000 | 20 — 30 mẫu | Cân bằng giữa tần suất và tốc độ |
| Trên 2.000 | 50+ mẫu | Dữ liệu lớn, mỗi lần retrain tốn thời gian, nên giảm tần suất |

#### ✏️ [CẬP NHẬT] Retrain mô hình

Quá trình retrain lấy toàn bộ dữ liệu lịch sử từ MongoDB bao gồm cả dataset gốc và các ca thực tế đã tích lũy, huấn luyện lại mô hình hồi quy tuyến tính, đánh giá kết quả trên tập kiểm thử, và chỉ kích hoạt version mới nếu chỉ số MAE không tệ hơn version hiện tại. File mô hình mới được lưu lại và thông tin về hiệu suất được ghi vào lịch sử version. Các bản ghi trong vùng đệm đã được dùng để retrain sẽ được đánh dấu để không tính vào lần retrain tiếp theo.

Ngoài retrain tự động, quản trị viên cũng có thể kích hoạt retrain thủ công bất kỳ lúc nào từ giao diện quản lý mà không cần chờ đủ điều kiện.

#### Lịch sử version mô hình

Hệ thống lưu lại thông tin của từng version mô hình bao gồm thời điểm huấn luyện, số mẫu dùng để train, các chỉ số MAE, RMSE, R² và trạng thái đang active hay không. Quản trị viên có thể xem so sánh hiệu suất giữa các version theo thời gian.

---

### 3.4 Dashboard và thống kê

Dashboard cung cấp cái nhìn tổng quan về hoạt động hệ thống với các thông tin sau:

**Thống kê tức thời** bao gồm tổng số bệnh nhân đang nằm viện, số bệnh nhân xuất viện trong ngày, số ca quá hạn cần chú ý và sai lệch dự đoán trung bình của tháng hiện tại.

**Biểu đồ phân tích** bao gồm đồ thị so sánh LOS dự đoán và thực tế theo thời gian, biểu đồ phân phối sai lệch dự đoán, và biểu đồ hiệu suất mô hình qua các lần retrain.

**Danh sách cảnh báo** liệt kê tất cả bệnh nhân đang ở mức cảnh báo vàng hoặc đỏ, sắp xếp theo mức độ ưu tiên để nhân viên y tế dễ dàng theo dõi và xử lý.

---

## 4. Luồng hoạt động tổng thể

Hệ thống vận hành theo ba luồng chính song song với nhau.

**Luồng nhập viện và dự đoán** bắt đầu từ lúc nhân viên y tế điền thông tin bệnh nhân, hệ thống nhận dữ liệu, gọi mô hình dự đoán, lưu kết quả và trả về ngày dự kiến xuất viện.

**Luồng theo dõi hàng ngày** được tự động hóa bởi Celery Beat, mỗi sáng hệ thống quét toàn bộ danh sách bệnh nhân đang nằm viện, so sánh ngày dự kiến với ngày hiện tại, cập nhật mức độ cảnh báo và đưa lên dashboard để nhân viên y tế nắm được ai cần được chú ý trong ngày.

**Luồng xuất viện và cải thiện mô hình** bắt đầu khi bệnh nhân xuất viện, hệ thống ghi nhận dữ liệu thực tế, so sánh với dự đoán, đưa vào vùng đệm và kiểm tra xem đã đủ ngưỡng retrain chưa. Nếu đủ, quá trình retrain được kích hoạt tự động trong nền mà không làm gián đoạn hoạt động của hệ thống.

---

## 5. Cơ sở dữ liệu

MongoDB được chọn vì cấu trúc dữ liệu bệnh nhân có nhiều trường tùy chọn và có thể thay đổi theo thời gian, phù hợp với tính linh hoạt của document-based database.

Hệ thống sử dụng bốn collection chính. **Collection patients** lưu toàn bộ thông tin hồ sơ bệnh nhân bao gồm features đầu vào, kết quả dự đoán, thông tin xuất viện và trạng thái cảnh báo. **Collection users** quản lý tài khoản và phân quyền. **Collection model_versions** lưu lịch sử các lần train mô hình cùng chỉ số hiệu suất. **Collection stream_buffer** lưu tạm dữ liệu chờ đủ ngưỡng để retrain.

---

## 6. Tính năng nổi bật của hệ thống

Điểm khác biệt so với một hệ thống dự đoán thông thường là khả năng **tự cải thiện theo thời gian**. Mô hình không đứng yên sau khi deploy mà liên tục được cập nhật dựa trên dữ liệu thực tế từ bệnh viện, giúp dự đoán ngày càng chính xác hơn theo từng đặc thù của cơ sở y tế.

**Cơ chế so sánh minh bạch** cho phép nhân viên y tế và quản trị viên luôn thấy được mức độ sai lệch giữa dự đoán và thực tế, tạo sự tin tưởng vào hệ thống và giúp phát hiện sớm khi mô hình đang hoạt động kém hiệu quả.

**Hệ thống cảnh báo chủ động** giúp nhân viên y tế không cần nhớ từng ca bệnh nhân mà vẫn được thông báo kịp thời về những trường hợp cần xem xét xuất viện hoặc đã quá hạn dự kiến.

---

## 7. Giới hạn và hướng mở rộng

Ở phiên bản hiện tại, hệ thống được thiết kế cho quy mô một cơ sở y tế với lượng dữ liệu vừa và nhỏ. Mô hình hồi quy tuyến tính đơn giản phù hợp cho mục đích học thuật nhưng có thể được thay thế bằng các thuật toán phức tạp hơn như Random Forest hoặc Gradient Boosting trong tương lai.

Hướng mở rộng bao gồm hỗ trợ đa cơ sở y tế với dữ liệu tách biệt, tích hợp thông báo qua email hoặc SMS khi có cảnh báo quan trọng, xuất báo cáo PDF định kỳ, và nâng cấp cơ chế streaming lên Kafka khi hệ thống cần xử lý lượng dữ liệu lớn từ nhiều nguồn đồng thời.

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