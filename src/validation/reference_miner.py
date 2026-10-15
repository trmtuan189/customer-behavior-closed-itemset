"""Bộ khai thác tập phổ biến THAM CHIẾU (độc lập với Apriori/FP-Growth/CHARM của nhóm).

Dùng Eclat với bitset (số nguyên Python): tidset của mỗi item là một số nguyên,
giao tidset = phép AND bit. Mục đích: làm "đáp án đối chứng" cho kiểm chứng CHARM.
"""
from __future__ import annotations
from collections import defaultdict


def mine_frequent(transactions, min_count):
    """Trả về dict {frozenset(items): support_count} cho MỌI tập phổ biến."""
    masks = defaultdict(int)
    for tid, basket in enumerate(transactions):
        bit = 1 << tid
        for item in basket:
            masks[item] |= bit

    singles = [(it, m, m.bit_count()) for it, m in masks.items() if m.bit_count() >= min_count]
    singles.sort(key=lambda t: (t[2], t[0]))
    result = {}

    def recurse(prefix, cands):
        # cands: list[(item, mask, count)] đã phổ biến khi nối với prefix
        for i, (item, mask, cnt) in enumerate(cands):
            new_prefix = prefix + (item,)
            result[frozenset(new_prefix)] = cnt
            nxt = []
            for item2, mask2, _ in cands[i + 1:]:
                m = mask & mask2
                c = m.bit_count()
                if c >= min_count:
                    nxt.append((item2, m, c))
            if nxt:
                recurse(new_prefix, nxt)

    recurse((), singles)
    return result
