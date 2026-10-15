"""Tiện ích dùng chung cho cả 3 thuật toán (Apriori, FP-Growth, CHARM)."""
from __future__ import annotations
import hashlib
import json
from decimal import Decimal, ROUND_CEILING
from pathlib import Path


def load_transactions(path):
    """Đọc transactions_v1.jsonl -> (list[frozenset], list[str] InvoiceNo, sha256)."""
    path = Path(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    baskets, invoices = [], []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            baskets.append(frozenset(row["items"]))
            invoices.append(str(row["InvoiceNo"]))
    return baskets, invoices, sha


def support_to_count(rate_text, n_transactions):
    """Đổi tỉ lệ support sang số giao dịch tối thiểu (làm tròn LÊN).
    Truyền chuỗi, ví dụ '0.01', để tránh sai số số thực."""
    return int((Decimal(rate_text) * n_transactions).to_integral_value(rounding=ROUND_CEILING))


def to_rows(itemsets, n_transactions):
    """dict {frozenset: count} -> danh sách dòng theo định dạng chuẩn của đề tài."""
    rows = [
        {"items": tuple(sorted(items)), "support_count": int(c), "support": c / n_transactions}
        for items, c in itemsets.items()
    ]
    rows.sort(key=lambda r: (r["support_count"], len(r["items"]), r["items"]), reverse=True)
    return rows


def save_rows(rows, path):
    with Path(path).open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({**r, "items": list(r["items"])}, ensure_ascii=False) + "\n")


def load_rows(path):
    """Đọc file kết quả (jsonl) -> dict {(items_tuple_sorted): support_count}."""
    out = {}
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                out[tuple(sorted(r["items"]))] = int(r["support_count"])
    return out
