# Khai thác hành vi khách hàng dựa trên tập mục phổ biến đóng

> **Mining Customer Behavior Based on Closed Frequent Itemsets** — Khoá luận tốt nghiệp ngành Khoa học Máy tính, Trường Đại học Công nghiệp TP. Hồ Chí Minh (IUH), năm 2026. Nhóm 2 sinh viên.

**English summary.** This repository implements and compares three frequent-itemset mining algorithms — **Apriori**, **FP-Growth**, and **CHARM** (closed itemsets) — on real transaction data (UCI *Online Retail II*, ~39.5k invoices after cleaning). The CHARM implementation is validated against an independent reference miner (all frequent itemsets → filter closed): it matches 100% at five support thresholds. Next steps are performance benchmarking, association-rule generation, customer-behavior analysis and a product-recommendation demo ("customers who bought X also bought Y").

---

## 1. Bài toán và mục tiêu

Trong bán lẻ, các sản phẩm thường được mua cùng nhau theo những quy luật ổn định. Khoá luận khai thác các quy luật đó từ giao dịch thật bằng **tập mục phổ biến đóng** (closed frequent itemsets): những tập sản phẩm được mua cùng nhau đủ nhiều và không có tập cha nào xuất hiện với số lần y hệt. Tập đóng giữ đủ thông tin của tập phổ biến nhưng gọn hơn khi dữ liệu có nhiều tập lồng nhau.

Mục tiêu:

1. Cài đặt và so sánh ba thuật toán: Apriori, FP-Growth (tập phổ biến) và CHARM (tập đóng).
2. Chứng minh cài đặt **đúng** bằng đối chiếu độc lập.
3. Sinh luật kết hợp từ tập đóng và diễn giải **hành vi mua sắm** của khách hàng.
4. Xây dựng **demo gợi ý sản phẩm mua kèm**.

## 2. Trạng thái dự án

| Hạng mục | Trạng thái |
|---|---|
| Khảo sát và làm sạch dữ liệu | Xong |
| Tạo cơ sở dữ liệu giao dịch `transactions_v1` | Xong |
| Cài đặt Apriori, FP-Growth | Xong, đã kiểm chứng đúng trên dữ liệu thật ở các mức support đã chạy |
| Cài đặt CHARM | Xong, khớp 100% với đáp án độc lập ở 5 mức support |
| Đo hiệu năng so sánh (thời gian, bộ nhớ, số tập, tỉ lệ nén) | Đang làm |
| Sinh luật kết hợp và lọc luật | Chưa làm |
| Phân tích hành vi khách hàng theo nhóm | Chưa làm |
| Demo gợi ý sản phẩm (web) | Có bản nguyên mẫu, chưa hoàn thiện |
| Báo cáo, slide, poster | Chưa làm |

## 3. Dữ liệu

- **Nguồn:** UCI Machine Learning Repository, bộ **Online Retail II** — giao dịch của một cửa hàng bán lẻ trực tuyến ở Anh (quà tặng, đồ gia dụng), giai đoạn 12/2009 đến 12/2011, lưu trong 2 sheet của file `online_retail_II.xlsx`.
- File gốc (khoảng 45 MB) **không đưa lên GitHub** (nằm trong `.gitignore`). Tải về và đặt tại `data/raw/online_retail_II.xlsx`.

### Quy tắc làm sạch (thực hiện trong `src/preprocessing/build_transactions_v1.py`)

1. Gộp hai sheet của file Excel.
2. Loại hoá đơn huỷ (số hoá đơn bắt đầu bằng chữ `C`).
3. Loại dòng có `Quantity <= 0` hoặc `UnitPrice <= 0`.
4. Loại các mã không phải hàng hoá: `B`, `ADJUST`, `ADJUST2`, `AMAZONFEE`, `BANK CHARGES`, `C2`, `DOT`, `POST`, `D`, `23444`, `M`, `m`.
5. **Mỗi hoá đơn là một giỏ hàng**; phần tử của giỏ là **mã sản phẩm (`StockCode`)**, không lặp, sắp xếp tăng dần.

### Kết quả sau làm sạch

| Chỉ số | Giá trị |
|---|---|
| Số dòng sau làm sạch | 1.037.032 |
| Số hoá đơn (giỏ hàng) | **39.529** |
| Giỏ chỉ có 1 sản phẩm | 3.227 |
| Số mã sản phẩm khác nhau | khoảng 4.900 |
| Giỏ lớn nhất | 1.109 sản phẩm (nhiều khả năng là đơn khách sỉ) |

File kết quả: `data/processed/transactions_v1.jsonl`, mỗi dòng là một giỏ:
```json
{"InvoiceNo": "489434", "items": ["21232", "21523", "21871", "22041", "22064", "79323P", "79323W", "85048"]}
```
SHA256 của file: `82af1569237c6973f24a2992792fb78bb893c85fe245ec4ffb3dba663d8cc38e` (dùng để đảm bảo mọi thuật toán chạy trên cùng một dữ liệu).

### Vì sao dùng mã sản phẩm thay vì tên sản phẩm

Phiên bản đầu của dự án dùng **tên mô tả** (`Description`) làm phần tử của giỏ. Khi so với bản dùng mã, có **4.172 hoá đơn có số món khác nhau**: một mã có thể mang hai tên mô tả khác nhau trong cùng hoá đơn (bị tính hai lần), và có những mã khác nhau dùng chung một tên (bị gộp lại). Tên mô tả không nhất quán nên mã sản phẩm là khoá đáng tin hơn. Bản dùng tên đã được cất khỏi pipeline.

## 4. Cấu trúc thư mục

```
customer-behavior-closed-itemset/
├── data/
│   ├── raw/                      # online_retail_II.xlsx (không đưa lên GitHub)
│   └── processed/
│       └── transactions_v1.jsonl # CSDL giao dịch dùng cho mọi thuật toán
├── results/                      # kết quả đối chiếu và đo đạc
│   ├── closed_check_summary.csv  # bảng đối chiếu CHARM với đáp án độc lập
│   └── closed_reference_*.jsonl  # tập đóng đáp án, theo từng mức support
└── src/
    ├── algorithms/
    │   ├── apriori.py            # Apriori (tập phổ biến)
    │   └── fp_growth.py          # FP-Growth (tập phổ biến)
    ├── preprocessing/
    │   └── build_transactions_v1.py   # dựng CSDL giao dịch từ file xlsx
    └── validation/
        ├── charm_impl.py         # CHARM (tập đóng)
        ├── reference_miner.py    # bộ khai thác tham chiếu độc lập (Eclat + bitset)
        ├── closed_filter.py      # lọc tập đóng từ tập phổ biến
        ├── check_closed.py       # đối chiếu CHARM với đáp án tham chiếu
        ├── run_baselines.py      # chạy Apriori/FP-Growth, đo thời gian, so khớp
        └── common.py             # hàm dùng chung (đọc dữ liệu, đổi support, lưu kết quả)
```

## 5. Cách chạy lại kết quả

**Yêu cầu:** Python từ 3.10 trở lên (mã dùng `int.bit_count`); thư viện `pandas`, `openpyxl` (chỉ cần cho bước dựng dữ liệu).

```
pip install pandas openpyxl
```

Chạy từ thư mục gốc của repo:

**Bước 1 — Dựng CSDL giao dịch** (khoảng 2 đến 3 phút; bỏ qua nếu đã có `transactions_v1.jsonl`):
```
python src/preprocessing/build_transactions_v1.py --xlsx data/raw/online_retail_II.xlsx --out data/processed/transactions_v1.jsonl
```
Kết quả đúng: `Dòng sau làm sạch: 1037032 | số hoá đơn: 39529 | giỏ 1 sản phẩm: 3227`.

**Bước 2 — Kiểm chứng CHARM:**
```
python src/validation/check_closed.py --data data/processed/transactions_v1.jsonl --out results
```
Kết quả đúng: 5 mức support đều báo `KHỚP 100%` và dòng cuối `TỔNG KẾT: TẤT CẢ KHỚP`.

**Bước 3 — Chạy Apriori và FP-Growth, đo thời gian, so khớp** (cần chạy Bước 2 trước):
```
python src/validation/run_baselines.py --data data/processed/transactions_v1.jsonl --algo-dir src/algorithms --out results --supports 0.05 0.03 0.02 --timeout 900
```
Mỗi lần chạy nằm trong một tiến trình riêng, có giới hạn thời gian; mức nào quá giờ được ghi "timeout" và các mức thấp hơn của thuật toán đó được bỏ qua. Kết quả ghi vào `results/baseline_timing.csv`.

## 6. Phương pháp kiểm chứng

Cài đặt khai thác dữ liệu rất dễ sai mà vẫn "chạy ra kết quả". Dự án kiểm chứng bằng ba lớp:

1. **Đáp án độc lập.** `reference_miner.py` khai thác **mọi** tập phổ biến bằng thuật toán Eclat với bitset (không dùng chung mã với ba thuật toán chính), sau đó `closed_filter.py` lọc ra tập đóng: một tập X là đóng khi không có tập cha nào hơn đúng 1 phần tử có cùng support.
2. **So khớp tuyệt đối.** `check_closed.py` so từng cặp (tập sản phẩm, số lần xuất hiện) giữa CHARM và đáp án; mọi chênh lệch (tập thừa, tập thiếu, lệch support) đều được liệt kê.
3. **Kiểm tra chéo bằng thư viện.** Bộ khai thác tham chiếu từng được đối chiếu với `mlxtend.fpgrowth` ở support 1% và cho kết quả trùng khít (1.342 tập).

CHARM còn được thử trên một bộ dữ liệu nhỏ 6 giao dịch (min_count = 3) có đáp án tính tay: **19 tập phổ biến, 7 tập đóng** — C (6), CW (5), CD (4), CT (4), ACW (4), CDW (3), ACTW (3) — và trên 300 bộ dữ liệu ngẫu nhiên nhỏ, đều khớp.

## 7. Kết quả hiện có

Đối chiếu CHARM với đáp án độc lập trên `transactions_v1` (39.529 giỏ):

| min_support | min_count | Tập phổ biến | Tập đóng | Tỉ lệ nén | CHARM khớp đáp án |
|---|---|---|---|---|---|
| 5% | 1.977 | 28 | 28 | 1,00 | Có |
| 3% | 1.186 | 110 | 110 | 1,00 | Có |
| 2% | 791 | 323 | 323 | 1,00 | Có |
| 1% | 396 | 1.342 | 1.342 | 1,00 | Có |
| 0,5% | 198 | 7.774 | 7.774 | 1,00 | Có |

`min_count` được tính bằng cách **làm tròn lên** tích của `min_support` với số giao dịch.

### Nhận xét quan trọng

- **Tập đóng gần như trùng tập phổ biến trên dữ liệu này** (tỉ lệ nén xấp xỉ 1,00). Nguyên nhân là dữ liệu thưa: khoảng 4.900 mã sản phẩm, giỏ trung vị chỉ vài chục món, nên hiếm khi có hai tập luôn xuất hiện cùng nhau với số lần y hệt. Tỉ lệ nén chỉ bắt đầu tách khỏi 1,00 ở support rất thấp (khoảng 0,15%), khi số tập đã lên tới hàng triệu. Điều này được thử trên nhiều cách biểu diễn khác (gộp biến thể màu, giỏ theo khách hàng) và không đổi. Lợi thế lý thuyết của tập đóng sẽ được minh hoạ thêm bằng bộ dữ liệu dày và bài toán nhỏ ở mục 6.
- **Apriori chậm ở support thấp** do phải kiểm tra từng ứng viên trên toàn bộ giao dịch. Thời gian chạy sẽ được đo và đưa vào `results/baseline_timing.csv`.

## 8. Lộ trình

- [x] Khảo sát và làm sạch dữ liệu
- [x] Cài đặt Apriori, FP-Growth, CHARM
- [x] Kiểm chứng CHARM bằng đáp án độc lập
- [ ] Đo hiệu năng so sánh: thời gian, bộ nhớ, số tập, tỉ lệ nén
- [ ] Sinh luật kết hợp (support, confidence, lift) từ tập đóng
- [ ] Phân tích hành vi theo nhóm khách hàng và theo mùa
- [ ] Demo web gợi ý sản phẩm mua kèm
- [ ] Báo cáo, slide, poster

## 9. Tài liệu tham khảo

- R. Agrawal, R. Srikant. *Fast Algorithms for Mining Association Rules.* VLDB, 1994. (Apriori)
- J. Han, J. Pei, Y. Yin. *Mining Frequent Patterns without Candidate Generation.* SIGMOD, 2000. (FP-Growth)
- M. J. Zaki, C.-J. Hsiao. *CHARM: An Efficient Algorithm for Closed Itemset Mining.* SIAM SDM, 2002. (CHARM)
- Bộ dữ liệu *Online Retail II*, UCI Machine Learning Repository.
