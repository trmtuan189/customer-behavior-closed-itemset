r"""Dựng transactions_v1.jsonl từ online_retail_II.xlsx theo đúng quy tắc làm sạch của notebook.
Kết quả mong đợi: 1.037.032 dòng sau làm sạch, 39.529 hoá đơn, 3.227 giỏ chỉ có 1 sản phẩm.

Chạy:  python build_transactions_v1.py --xlsx D:\KhoaLuan\data\raw\online_retail_II.xlsx --out D:\KhoaLuan\data\transactions_v1.jsonl
Cần: pip install pandas openpyxl
"""
import argparse, json
import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("--xlsx", required=True)
ap.add_argument("--out", required=True)
a = ap.parse_args()

x = pd.ExcelFile(a.xlsx, engine="openpyxl")
df = pd.concat([pd.read_excel(x, s) for s in x.sheet_names], ignore_index=True)
df = df.rename(columns={"Invoice": "InvoiceNo", "Price": "UnitPrice"})
for c in ["InvoiceNo", "StockCode"]:
    df[c] = df[c].astype("string").str.strip()

df = df[~df.InvoiceNo.str.upper().str.startswith("C", na=False)]   # bỏ hoá đơn huỷ
df = df[df.Quantity > 0]
bad = {"B", "ADJUST", "ADJUST2", "AMAZONFEE", "BANK CHARGES", "C2", "DOT", "POST", "D", "23444", "M", "m"}
df = df[~df.StockCode.isin(bad)]                                     # bỏ mã phí/điều chỉnh/manual
df = df[df.UnitPrice > 0]

g = df.groupby("InvoiceNo")["StockCode"].agg(lambda s: sorted(set(s))).reset_index(name="items").sort_values("InvoiceNo")
with open(a.out, "w", encoding="utf-8") as f:
    for inv, items in zip(g.InvoiceNo, g["items"]):
        f.write(json.dumps({"InvoiceNo": str(inv), "items": items}, ensure_ascii=False) + "\n")
print("Dòng sau làm sạch:", len(df), "| số hoá đơn:", len(g), "| giỏ 1 sản phẩm:", int((g["items"].str.len() == 1).sum()))
