# Kết quả thực nghiệm ban đầu

## 1. Dữ liệu thực nghiệm

Các thuật toán được thực nghiệm trên cùng bộ dữ liệu gồm 39.529 giao dịch. Mỗi giao dịch là một hóa đơn chứa các mã sản phẩm không lặp.

Apriori và FP-Growth khai thác tập mục phổ biến. Kết quả của hai thuật toán sau đó được lọc thành tập đóng để đối chiếu với đáp án tham chiếu.

## 2. Kết quả Apriori và FP-Growth

| Min support | Số giao dịch tối thiểu | Số tập phổ biến | Số tập đóng sau lọc | FP-Growth (giây) | Apriori (giây) | Tập đóng khớp tham chiếu |
|---|---:|---:|---:|---:|---:|---|
| 5% | 1.977 | 28 | 28 | 1,09 | 18,32 | Có |
| 3% | 1.186 | 110 | 110 | 16,58 | 32,89 | Có |
| 2% | 791 | 323 | 323 | 39,00 | 244,05 | Có |

Nguồn: results/baseline_timing.csv. Các số giao dịch tối thiểu được tính bằng cách làm tròn lên tích của min support với 39.529.

## 3. Nhận xét

Khi giảm min support từ 5% xuống 2%, số tập phổ biến tăng từ 28 lên 323. Thời gian chạy của cả hai cài đặt cũng tăng.

Trong ba cấu hình đã đo, cài đặt FP-Growth của nhóm chạy nhanh hơn cài đặt Apriori. Tại support 2%, Apriori mất 244,05 giây, trong khi FP-Growth mất 39,00 giây, tương ứng Apriori mất thời gian khoảng 6,26 lần FP-Growth.

Ở cả ba ngưỡng, số tập đóng bằng số tập phổ biến. Vì vậy, các cấu hình này chưa thể hiện lợi ích giảm số lượng tập khi sử dụng biểu diễn tập đóng.

Tập đóng được lọc từ đầu ra của cả hai thuật toán đều khớp đáp án tham chiếu về các tập sản phẩm và support count.

## 4. Giới hạn của kết quả

Mỗi cấu hình trong bảng hiện có một lần đo. Kết quả phản ánh thời gian quan sát được của các cài đặt trên máy thực nghiệm, chưa đủ để khái quát rằng một thuật toán luôn nhanh hơn thuật toán khác.

Thời gian trong bảng được đo quanh phần gọi thuật toán, không bao gồm bước lọc tập đóng để đối chiếu. Phép đo hiện bao gồm thao tác import thuật toán trong đoạn được bấm giờ.

Bảng chưa có số liệu bộ nhớ và chưa đưa CHARM vào so sánh thời gian trực tiếp. Cần chạy CHARM tại cùng ngưỡng support và thống nhất phạm vi đo trước khi lập bảng so sánh ba thuật toán.
## 5. Kết quả kiểm chứng CHARM mới nhất

CHARM được chạy trên cùng bộ dữ liệu 39.529 giao dịch tại ba ngưỡng support 5%, 3% và 2%.

| Min support | Số giao dịch tối thiểu | Số tập đóng | Thời gian CHARM (giây) | Khớp tham chiếu |
|---|---:|---:|---:|---|
| 5% | 1.977 | 28 | 0,71 | Có |
| 3% | 1.186 | 110 | 1,32 | Có |
| 2% | 791 | 323 | 2,94 | Có |

Nguồn: results/charm_latest/closed_check_summary.csv.

Ở cả ba ngưỡng, đầu ra CHARM khớp bộ tham chiếu về tập sản phẩm và support count. Khi giảm support, số tập đóng và thời gian chạy quan sát được đều tăng.

Các số liệu này là kết quả một lần chạy cho mỗi cấu hình. Thời gian được đo quanh lời gọi hàm CHARM, bao gồm dựng dữ liệu dọc và kiểm tra tính đóng cuối cùng, không bao gồm đọc file dữ liệu.

Chưa sử dụng các số liệu này để tính tỷ lệ tăng tốc so với baseline: phép đo baseline hiện bao gồm import thuật toán, trong khi phép đo CHARM không bao gồm phần import. Cần thống nhất cách đo và chạy lặp trước khi đưa ra kết luận so sánh hiệu năng cuối cùng.
## 6. Kết quả đo bằng chương trình benchmark chung

Chương trình chạy mỗi thuật toán trong tiến trình riêng, yêu cầu 3 lần chạy tại mỗi ngưỡng support. Thời gian đo không bao gồm import, đọc dữ liệu và kiểm chứng.

| Min support | CHARM (giây) | FP-Growth (giây) | Apriori (giây) |
|---|---:|---:|---:|
| 5% | 0,772 | 0,665 | 16,346 |
| 3% | 1,030 | 11,656 | 35,224 |
| 2% | 2,184 | 32,675 | 246,098* |

Nguồn: results/benchmark_all/summary.csv và runs.csv.

Các giá trị là trung vị của 3 lần hợp lệ, ngoại trừ Apriori tại support 2% chỉ có 2 lần hợp lệ. Lần thứ ba của cấu hình này vượt giới hạn 900 giây của toàn tiến trình, bao gồm chuẩn bị, khai thác và kiểm chứng. Chưa xác định bước nào gây vượt thời gian.

Ở support 5%, FP-Growth có thời gian trung vị thấp nhất. Ở support 3% và 2%, CHARM có thời gian trung vị thấp nhất trong các lần chạy thành công.

Các lần chạy được ghi nhận hợp lệ đều khớp bộ tham chiếu. Apriori và FP-Growth được đối chiếu toàn bộ tập phổ biến; CHARM được đối chiếu tập đóng.

Chỉ số bộ nhớ trong file kết quả là bộ nhớ đỉnh của toàn tiến trình trước bước kiểm chứng, bao gồm thư viện và dữ liệu đầu vào. Đây không phải bộ nhớ riêng của thuật toán. CHARM hiện import pandas, trong khi hai baseline không có cùng phụ thuộc này, nên cần thận trọng khi diễn giải chênh lệch bộ nhớ.
## 7. Kiểm tra bổ sung Apriori tại support 2%

Sau khi một lần chạy trong benchmark chung bị timeout, nhóm thực hiện một lần kiểm tra bổ sung riêng cho Apriori tại support 2%.

Kết quả:

- Số giao dịch: 39.529.
- Support count tối thiểu: 791.
- Số tập phổ biến: 323.
- Thời gian khai thác: 269,82 giây.
- Kết quả khớp bộ tham chiếu.
- Nhật ký xác nhận hoàn thành bước khai thác và kiểm chứng.

Nguồn: results/apriori_002_diagnostic/runs.csv và Apriori_0.02_1.log.

Lần kiểm tra bổ sung thành công cho thấy cấu hình này có thể hoàn thành và trả kết quả đúng. Nguyên nhân timeout của lần chạy trước chưa xác định. Kết quả bổ sung được trình bày riêng, không thay thế hoặc xóa bản ghi timeout trong benchmark ban đầu.
## 8. Kiểm chứng luật kết hợp

Nhóm kiểm tra độc lập các luật đã xuất bằng cách đếm trực tiếp trên cơ sở dữ liệu 39.529 giao dịch, không sử dụng lại cơ chế tra support từ tập đóng.

Với mỗi luật X → Y, chương trình đếm số giao dịch chứa X, chứa Y và chứa đồng thời X ∪ Y. Từ đó tính lại support, confidence và lift, rồi đối chiếu với các giá trị đã xuất theo độ làm tròn tương ứng.

Kết quả kiểm tra:

- Tổng số luật: 7.452.
- Số luật vượt qua kiểm tra: 7.452.
- Số luật không vượt qua kiểm tra: 0.

Chương trình cũng kiểm tra cấu trúc luật, luật trùng và các ngưỡng support, confidence, lift.

Nguồn: results/rule_validation/summary.json và checked_rules.csv.

Kết quả xác nhận tính nhất quán về số đếm và các chỉ số của những luật đã xuất. Phép kiểm tra chưa chứng minh tính đầy đủ của tập luật, tính đúng của nhãn nhóm sản phẩm hoặc hiệu quả ứng dụng kinh doanh.