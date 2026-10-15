"""Lọc tập ĐÓNG từ tập các tập phổ biến.

X là đóng  <=>  không tồn tại tập cha Y ⊃ X có cùng support.
Nhờ tính phản đơn điệu, chỉ cần kiểm tra các tập cha hơn đúng 1 phần tử:
nếu có tập cha cùng support thì tập trung gian cũng cùng support.
"""
from __future__ import annotations


def filter_closed(frequent):
    """frequent: dict {frozenset: count} đầy đủ (mọi tập phổ biến)."""
    not_closed = set()
    for y, cy in frequent.items():
        if len(y) < 2:
            continue
        for item in y:
            x = y - {item}
            if x in frequent and frequent[x] == cy:
                not_closed.add(x)
    return {x: c for x, c in frequent.items() if x not in not_closed}
