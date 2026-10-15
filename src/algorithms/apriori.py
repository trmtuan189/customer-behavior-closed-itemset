"""
Cai dat thuat toan APRIORI
Muc dich: sinh ra tap FREQUENT ITEMSETS (khong phai closed) tu transaction database.
Day la thuat toan baseline don gian nhat, lam nen tang truoc khi hoc CHARM.
"""

from itertools import combinations


def get_support_counts(transactions, candidates):
    """
    Tinh so lan xuat hien (support count) cua tung candidate itemset
    trong toan bo transaction database.

    transactions: list cac frozenset, moi frozenset la 1 giao dich
    candidates: list cac frozenset can tinh support count
    """
    counts = {c: 0 for c in candidates}
    for t in transactions:
        for c in candidates:
            if c.issubset(t):
                counts[c] += 1
    return counts


def generate_candidates(prev_frequent_itemsets, k):
    """
    Sinh candidate itemset kich thuoc k tu cac frequent itemset kich thuoc k-1.
    Dung ky thuat join: ghep 2 itemset (k-1) co chung k-2 phan tu dau.
    """
    candidates = set()
    prev_list = list(prev_frequent_itemsets)
    for i in range(len(prev_list)):
        for j in range(i + 1, len(prev_list)):
            union = prev_list[i] | prev_list[j]
            if len(union) == k:
                candidates.add(union)
    return candidates


def prune_candidates(candidates, prev_frequent_itemsets, k):
    """
    Loai bo candidate co chua tap con (k-1) khong nam trong prev_frequent_itemsets.
    Day la nguyen ly Apriori: neu 1 tap khong pho bien thi tap cha cung khong pho bien.
    """
    pruned = set()
    for c in candidates:
        subsets = combinations(c, k - 1)
        if all(frozenset(s) in prev_frequent_itemsets for s in subsets):
            pruned.add(c)
    return pruned


def apriori(transactions_raw, min_support, max_k=None):
    """
    Ham chinh chay thuat toan Apriori.

    transactions_raw: list cac list/set, vi du [['A','B','C'], ['A','B']]
    min_support: nguong ty le (vi du 0.5 nghia la 50%)
    max_k: gioi han kich thuoc itemset lon nhat (None = khong gioi han, chay het)

    Tra ve: dict {frozenset: support_count} cho tat ca frequent itemsets
    LUU Y: truoc day ban demo tren du lieu that co gioi han thu cong o level 3,
    khien ket qua THIEU SO VOI THUC TE (thieu 1 tap 4-itemset). Ham nay da sua
    de mac dinh chay den khi khong con candidate nao (giong FP-Growth).
    """
    transactions = [frozenset(t) for t in transactions_raw]
    n_transactions = len(transactions)
    min_count = min_support * n_transactions

    # Buoc 1: sinh 1-itemset tu tat ca item xuat hien
    all_items = set()
    for t in transactions:
        all_items.update(t)
    candidates_1 = [frozenset([item]) for item in all_items]

    counts = get_support_counts(transactions, candidates_1)
    frequent_itemsets = {}
    current_frequent = set()
    for itemset, count in counts.items():
        if count >= min_count:
            frequent_itemsets[itemset] = count
            current_frequent.add(itemset)

    k = 2
    while current_frequent:
        if max_k is not None and k > max_k:
            break
        candidates = generate_candidates(current_frequent, k)
        candidates = prune_candidates(candidates, current_frequent, k)
        if not candidates:
            break
        counts = get_support_counts(transactions, candidates)
        current_frequent = set()
        for itemset, count in counts.items():
            if count >= min_count:
                frequent_itemsets[itemset] = count
                current_frequent.add(itemset)
        k += 1

    return frequent_itemsets, n_transactions


def print_results(frequent_itemsets, n_transactions):
    """In ket qua ra man hinh, sap xep theo kich thuoc itemset roi theo support."""
    sorted_items = sorted(frequent_itemsets.items(), key=lambda x: (len(x[0]), -x[1]))
    print(f"{'Itemset':<25} {'Count':<8} {'Support':<10}")
    print("-" * 45)
    for itemset, count in sorted_items:
        support = count / n_transactions
        items_str = "{" + ", ".join(sorted(itemset)) + "}"
        print(f"{items_str:<25} {count:<8} {support:<10.2%}")


if __name__ == "__main__":
    # ============================================
    # TEST CASE NHO - TU TINH TAY DE DOI CHIEU
    # ============================================
    # 5 giao dich, giong vi du trong lo trinh khoa luan
    test_transactions = [
        ["A", "B", "C"],
        ["A", "B"],
        ["A", "C"],
        ["B", "C"],
        ["A", "B", "C"],
    ]

    print("=== TEST TREN DU LIEU NHO (5 giao dich) ===")
    print("Du lieu:")
    for i, t in enumerate(test_transactions, 1):
        print(f"  T{i} = {{{', '.join(t)}}}")
    print()

    min_sup = 0.6  # 60% -> can xuat hien trong >= 3/5 giao dich
    print(f"min_support = {min_sup} (tuc >= {min_sup*5:.0f}/5 giao dich)")
    print()

    freq_itemsets, n = apriori(test_transactions, min_sup)
    print_results(freq_itemsets, n)

    print()
    print("=== DOI CHIEU TU TINH TAY ===")
    print("A xuat hien trong T1,T2,T3,T5 -> count=4, support=80%  -> frequent")
    print("B xuat hien trong T1,T2,T4,T5 -> count=4, support=80%  -> frequent")
    print("C xuat hien trong T1,T3,T4,T5 -> count=4, support=80%  -> frequent")
    print("{A,B} trong T1,T2,T5 -> count=3, support=60% -> frequent (dung nguong)")
    print("{A,C} trong T1,T3,T5 -> count=3, support=60% -> frequent (dung nguong)")
    print("{B,C} trong T1,T4,T5 -> count=3, support=60% -> frequent (dung nguong)")
    print("{A,B,C} trong T1,T5  -> count=2, support=40% -> KHONG frequent (duoi nguong)")
