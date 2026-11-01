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

## 4. Phát hiện thứ ba: đĩa giấy và khăn giấy thường được mua cùng nhau

Luật: Set/6 Red Spotty Paper Plates → Set/20 Red Retrospot Paper Napkins.

Đây là luật số 12 trong results/findings_0_005.md.

### Số liệu

- Số hóa đơn chứa cả hai sản phẩm: 685.
- Support: 1,73%.
- Confidence: 69,6%.
- Lift: khoảng 13,0.

### Giải thích

Có 685 hóa đơn chứa đồng thời bộ đĩa giấy và bộ khăn giấy này, chiếm khoảng 1,73% trong 39.529 giao dịch.

Trong các hóa đơn có bộ đĩa giấy, khoảng 69,6% cũng có bộ khăn giấy. Tỷ lệ này cao gấp khoảng 13 lần tỷ lệ xuất hiện của bộ khăn giấy trong toàn bộ hóa đơn.

Dựa trên tên, hai sản phẩm khác loại nhưng có thể bổ trợ về công dụng trong bối cảnh sử dụng bàn ăn hoặc bàn tiệc. Đây là giả thuyết diễn giải từ tên sản phẩm và quan hệ đồng xuất hiện, chưa phải bằng chứng về mục đích mua thực tế.

### Đề xuất ứng dụng

Có thể thử gợi ý bộ khăn giấy khi khách chọn bộ đĩa giấy, hoặc thử trưng bày hai sản phẩm gần nhau. Hiệu quả cần được đánh giá riêng trước khi khẳng định làm tăng tỷ lệ mua hoặc doanh thu.

### Giới hạn

Dữ liệu chưa xác nhận khách mua để tổ chức tiệc, sử dụng cá nhân hay bán lại. Luật không xác định thứ tự mua và không phải quan hệ nhân quả. Phân loại bổ trợ dựa trên tên, chưa đối chiếu danh mục chính thức.

## 5. Phân biệt các quan hệ sản phẩm trong ba ví dụ

| Ví dụ | Diễn giải sơ bộ từ tên sản phẩm | Phạm vi kết luận |
|---|---|---|
| Hai mẫu túi | Sản phẩm tương đồng, khác mẫu | Đồng xuất hiện giữa các mẫu túi |
| Hai mẫu hộp gốm | Sản phẩm tương đồng, khác mẫu | Đồng xuất hiện giữa các mẫu hộp |
| Đĩa giấy và khăn giấy | Khác loại, có thể bổ trợ về công dụng | Giả thuyết gợi ý mua kèm, cần đánh giá thêm |

Không dùng nhãn khác nhóm do script tự suy ra để khẳng định cả ba ví dụ đều là mua chéo giữa những ngành hàng khác nhau. Đơn vị thống kê là hóa đơn, không phải khách hàng duy nhất.

## 6. Phát hiện thứ tư: hai mẫu tách Regency thường được mua cùng nhau

Luật: Green Regency Teacup And Saucer → Roses Regency Teacup And Saucer (luật số 6 trong bảng phát hiện).

### Số liệu và diễn giải

Hai bộ tách và đĩa lót cùng xuất hiện trong 1.034 hóa đơn, tương ứng support khoảng 2,62%. Trong các hóa đơn có mẫu Green Regency, khoảng 76,4% cũng có mẫu Roses Regency. Lift khoảng 21,2 cho thấy tỷ lệ có mẫu Roses trong nhóm hóa đơn chứa mẫu Green cao gấp khoảng 21,2 lần tỷ lệ chung.

Dựa trên tên sản phẩm, đây là quan hệ đồng xuất hiện giữa hai mẫu trong cùng dòng Regency. Có thể thử giới thiệu mẫu Roses khi khách xem mẫu Green, với giả thuyết khách quan tâm nhiều mẫu phối hợp.

### Giới hạn

Chưa xác nhận khách muốn phối bộ, sưu tập hay mua để bán lại. Lift cao không chứng minh hiệu quả kinh doanh cao hơn các luật khác; cần xem cả số giao dịch, confidence và kết quả đánh giá ứng dụng. Hai sản phẩm cùng loại nên không gọi đây là mua chéo giữa các ngành hàng.

## 7. Phát hiện thứ năm: hai mẫu hộp cơm thường được mua cùng nhau

Luật: Dolly Girl Lunch Box → Spaceboy Lunch Box (luật số 8 trong bảng phát hiện).

### Số liệu và diễn giải

Hai mẫu hộp cơm cùng xuất hiện trong 829 hóa đơn, chiếm khoảng 2,10% tổng giao dịch. Trong các hóa đơn có Dolly Girl Lunch Box, khoảng 62,2% cũng có Spaceboy Lunch Box. Lift khoảng 18,0 cho thấy tỷ lệ có Spaceboy trong nhóm hóa đơn chứa Dolly Girl cao gấp khoảng 18 lần tỷ lệ chung.

Theo tên, đây là hai mẫu hộp cơm tương đồng. Có thể thử hiển thị mẫu Spaceboy trong danh sách mẫu liên quan khi khách chọn Dolly Girl, đồng thời theo dõi hiệu quả thực tế của gợi ý.

### Giới hạn

Không suy ra giới tính người sử dụng, số trẻ trong gia đình hoặc mục đích mua quà từ tên sản phẩm. Hóa đơn có hai mẫu chưa xác nhận cùng một cá nhân sử dụng cả hai.

## 8. Tổng hợp và phạm vi ứng dụng

Năm ví dụ được chọn để minh họa, không phải mẫu đại diện thống kê của toàn bộ luật. Bốn ví dụ mô tả các sản phẩm tương đồng; một ví dụ gợi ý quan hệ bổ trợ về công dụng. Phân loại này dựa trên tên, cần đối chiếu danh mục nếu muốn dùng như nhãn chính thức.

Ứng dụng dự kiến là gợi ý mua kèm dựa trên luật lịch sử. Khi hiển thị, cần nêu sản phẩm, số hóa đơn, confidence và lift. Các chỉ số này không phải xác suất mua trong tương lai đã được kiểm định. Muốn đánh giá gợi ý cho tương lai cần dữ liệu đánh giá riêng, ưu tiên chia theo thời gian.
