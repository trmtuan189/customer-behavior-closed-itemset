# Phân tích hành vi mua sắm từ luật kết hợp

## 1. Dữ liệu và kết quả khai thác

Mỗi hóa đơn được biểu diễn thành một giỏ hàng gồm các mã sản phẩm không lặp. Bộ dữ liệu sau xử lý gồm 39.529 giỏ hàng.

Ở ngưỡng support 0,5%, một tập sản phẩm phải xuất hiện trong ít nhất 198 hóa đơn để được xem là phổ biến. Thuật toán CHARM khai thác được 7.774 tập phổ biến đóng.

Chương trình sinh luật có vế trái tối đa 3 sản phẩm và vế phải 1 sản phẩm, bằng cách tách các tập đóng có từ 2 đến 4 sản phẩm. Với confidence tối thiểu 30% và lift tối thiểu 1,5, chương trình thu được 7.452 luật theo phạm vi sinh luật này.

## 2. Phát hiện đầu tiên: hai loại túi thường được mua cùng nhau

Luật: Jumbo Storage Bag Suki → Jumbo Bag Red Retrospot.

### Số liệu

- Số hóa đơn chứa cả hai sản phẩm: 1.324.
- Support: 3,35%.
- Confidence: 56,8%.
- Lift: khoảng 5,6.

### Giải thích

Trong toàn bộ 39.529 hóa đơn, có 1.324 hóa đơn chứa đồng thời hai sản phẩm, tương ứng khoảng 3,35%.

Trong những hóa đơn có Jumbo Storage Bag Suki, khoảng 56,8% cũng có Jumbo Bag Red Retrospot.

Lift khoảng 5,6 cho thấy tỷ lệ có Jumbo Bag Red Retrospot trong các hóa đơn chứa Suki cao gấp khoảng 5,6 lần tỷ lệ có Red Retrospot trong toàn bộ hóa đơn.

### Đề xuất ứng dụng

Có thể thử hiển thị Jumbo Bag Red Retrospot trong mục gợi ý mua kèm khi khách chọn Jumbo Storage Bag Suki.

### Giới hạn

Kết quả thể hiện quan hệ xuất hiện cùng nhau trong hóa đơn lịch sử. Kết quả chưa chứng minh khách mua sản phẩm nào trước, lý do mua cùng, hoặc việc gợi ý sẽ làm tăng doanh thu.
## 3. Phát hiện thứ hai: hai loại hộp đựng đồ nhỏ thường được mua cùng nhau

Luật: Sweetheart Ceramic Trinket Box → Strawberry Ceramic Trinket Box.

### Số liệu

- Số hóa đơn chứa cả hai sản phẩm: 1.109.
- Support: 2,81%.
- Confidence: 74,8%.
- Lift: khoảng 12,1.

### Giải thích

Trong toàn bộ dữ liệu, có 1.109 hóa đơn chứa đồng thời hai loại hộp gốm đựng đồ nhỏ này, tương ứng khoảng 2,81% tổng số hóa đơn.

Trong những hóa đơn có Sweetheart Ceramic Trinket Box, khoảng 74,8% cũng có Strawberry Ceramic Trinket Box.

Lift khoảng 12,1 cho thấy tỷ lệ có hộp Strawberry trong nhóm hóa đơn chứa hộp Sweetheart cao gấp khoảng 12,1 lần tỷ lệ có hộp Strawberry trong toàn bộ hóa đơn.

Kết quả cho thấy hai sản phẩm có xu hướng được mua cùng nhau. Tuy nhiên, dữ liệu hóa đơn chưa cho biết mục đích mua là sử dụng, làm quà hay bán lại.

### Đề xuất ứng dụng

Có thể thử giới thiệu hộp Strawberry trong mục sản phẩm mua kèm khi khách xem hoặc chọn hộp Sweetheart. Cửa hàng cũng có thể thử trưng bày hai sản phẩm gần nhau.

### Giới hạn

Luật được rút ra từ dữ liệu lịch sử và chưa được đánh giá trên dữ liệu mới. Confidence cao chưa đủ để khẳng định đề xuất mua kèm sẽ đem lại hiệu quả kinh doanh.