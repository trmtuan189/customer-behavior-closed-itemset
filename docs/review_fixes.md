# Rà soát trước khi chốt thực nghiệm — 29/10/2026

## Đã sửa và kiểm tra

- Sửa tiêu đề tiếng Việt của findings_0_005.csv sang UTF-8 BOM; giữ nguyên toàn bộ dòng dữ liệu. Bản trước sửa nằm trong archive_v0, không theo dõi bằng Git.
- Tính lại SHA256: bản CRLF là ad35aa8fc91b0ac3b4e249d476124f970dbb9c088a4b71badd857f1c9b2fe7da; sau chuẩn hóa LF là 82af1569237c6973f24a2992792fb78bb893c85fe245ec4ffb3dba663d8cc38e. Đã sửa README. Trùng số lượng kết quả không thay thế kiểm tra nội dung dữ liệu.
- Bỏ import pandas và các import không dùng trong charm_impl.py. Không đổi logic khai thác. Các số đo cũ vẫn bao gồm pandas và phải giữ nguyên.
- Benchmark đã có nhật ký giai đoạn và chọn thuật toán riêng. Lần kiểm tra riêng Apriori 2% hoàn thành, nhưng nguyên nhân timeout cũ vẫn chưa xác định.

## Cần hoàn thành trước khi chốt báo cáo

1. Chạy benchmark mới sau khi bỏ pandas, dùng thư mục riêng results/benchmark_clean_imports. Giữ nguyên mọi kết quả cũ; không ghép chọn các lần nhanh từ nhiều phiên.
2. Nếu còn timeout, dùng log giai đoạn để điều tra; không khẳng định nguyên nhân khi chưa có bằng chứng. Báo số lần hợp lệ, cả các thất bại và thời gian của những lần hoàn thành.
3. Giới hạn kết luận bộ nhớ ở bộ nhớ đỉnh tiến trình, gồm môi trường và dữ liệu; chưa gọi đó là bộ nhớ riêng thuật toán.
4. Rà soát thủ công nhãn cùng/khác nhóm của những luật đưa vào báo cáo. Nhãn hiện là heuristic từ mã/tên, không phải phân loại chính thức. Không diễn giải 3.931 luật là cross-sell đã được xác thực.
5. Phạm vi luật: vế phải một sản phẩm, vế trái 1–3 sản phẩm, hợp hai vế là một tập đóng. Kiểm chứng 7.452 luật chỉ xác nhận các luật đã xuất, không chứng minh liệt kê đủ mọi luật.
6. Tập đóng bằng tập phổ biến ở các cấu hình đã lưu là kết quả thực nghiệm, không phải lỗi cần thay số liệu. Muốn minh họa nén phải bổ sung ví dụ hoặc thực nghiệm riêng.

## Quy tắc dùng số liệu

- Giữ bảng benchmark ban đầu như kết quả lịch sử; kết quả sau sửa import là một phiên mới.
- Không thay timeout cũ bằng một lần chạy lại thành công.
- Không kết luận quan hệ nhân quả, cá nhân hóa hoặc hiệu quả tăng doanh thu từ luật đồng xuất hiện.
- Đây là bản rà soát các điểm đã nhận diện, chưa phải xác nhận toàn bộ dự án đã hoàn thiện.
