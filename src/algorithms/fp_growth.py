"""
Cai dat thuat toan FP-GROWTH
Muc dich: sinh ra tap FREQUENT ITEMSETS (giong Apriori) nhung khong can sinh candidate
tuong minh - dung cau truc cay FP-Tree de nen du lieu va khai thac truc tiep.

Y tuong chinh (khac hoan toan Apriori):
1. Dem support cua tung item, loai bo item khong pho bien.
2. Sap xep item trong moi giao dich theo thu tu GIAM DAN support (quan trong!).
3. Xay FP-Tree: cac giao dich co chung tien to (prefix) se dung chung nhanh cay
   -> nen du lieu, giam so lan phai duyet lai toan bo database.
4. Khai thac de quy tu duoi len (bottom-up): voi moi item trong header table,
   lay "conditional pattern base" (cac duong di tu goc den node cua item do),
   xay "conditional FP-tree" tu do, roi de quy tiep.
"""

from collections import defaultdict


class FPNode:
    def __init__(self, item, count, parent):
        self.item = item
        self.count = count
        self.parent = parent
        self.children = {}   # item -> FPNode
        self.link = None     # lien ket toi node ke tiep cung ten item (cho header table)

    def increment(self, count):
        self.count += count


def build_fptree(transactions, min_count):
    """
    Xay FP-Tree tu danh sach giao dich.
    transactions: list cac list item, vi du [['A','B','C'], ['A','B']]
    Tra ve: (root, header_table)
      header_table: dict item -> [support_count, node_link_dau_tien]
    """
    # Buoc 1: dem support tung item
    item_counts = defaultdict(int)
    for t in transactions:
        for item in set(t):
            item_counts[item] += 1

    # Loai item khong dat min_count
    item_counts = {k: v for k, v in item_counts.items() if v >= min_count}
    if not item_counts:
        return None, {}

    header_table = {item: [count, None] for item, count in item_counts.items()}

    root = FPNode(None, 0, None)

    for t in transactions:
        # Chi giu item pho bien, sap xep GIAM DAN theo support (rat quan trong de cay gon)
        filtered = [item for item in t if item in item_counts]
        filtered.sort(key=lambda x: (-item_counts[x], x))

        current_node = root
        for item in filtered:
            if item in current_node.children:
                current_node.children[item].increment(1)
            else:
                new_node = FPNode(item, 1, current_node)
                current_node.children[item] = new_node
                # Noi vao header table (linked list cac node cung ten item)
                if header_table[item][1] is None:
                    header_table[item][1] = new_node
                else:
                    node = header_table[item][1]
                    while node.link is not None:
                        node = node.link
                    node.link = new_node
            current_node = current_node.children[item]

    return root, header_table


def get_prefix_paths(item, header_table):
    """
    Lay tat ca "conditional pattern base" cua 1 item: duong di tu goc
    (khong tinh chinh no) den moi node cua item do, kem trong so (count cua node).
    """
    paths = []
    node = header_table[item][1]
    while node is not None:
        path = []
        parent = node.parent
        while parent is not None and parent.item is not None:
            path.append(parent.item)
            parent = parent.parent
        if path:
            paths.append((list(reversed(path)), node.count))
        node = node.link
    return paths


def fp_growth(transactions, min_support, min_count=None):
    """
    Ham chinh. Tra ve dict {frozenset: support_count}
    """
    n = len(transactions)
    if min_count is None:
        min_count = min_support * n

    frequent_itemsets = {}

    def mine(tree_transactions, suffix, min_count):
        root, header_table = build_fptree(tree_transactions, min_count)
        if root is None:
            return
        # Duyet tu item co support THAP nhat len cao nhat (chuan FP-Growth)
        items_sorted = sorted(header_table.items(), key=lambda x: x[1][0])
        for item, (count, _) in items_sorted:
            new_itemset = frozenset(suffix | {item})
            frequent_itemsets[new_itemset] = count

            # Xay conditional pattern base -> conditional transactions
            paths = get_prefix_paths(item, header_table)
            conditional_transactions = []
            for path, path_count in paths:
                for _ in range(path_count):
                    conditional_transactions.append(path)

            if conditional_transactions:
                mine(conditional_transactions, suffix | {item}, min_count)

    mine(transactions, set(), min_count)
    return frequent_itemsets, n


def print_results(frequent_itemsets, n_transactions):
    sorted_items = sorted(frequent_itemsets.items(), key=lambda x: (len(x[0]), -x[1]))
    print(f"{'Itemset':<25} {'Count':<8} {'Support':<10}")
    print("-" * 45)
    for itemset, count in sorted_items:
        support = count / n_transactions
        items_str = "{" + ", ".join(sorted(itemset)) + "}"
        print(f"{items_str:<25} {count:<8} {support:<10.2%}")


if __name__ == "__main__":
    # Dung DUNG bo du lieu 5 giao dich giong het luc test Apriori de doi chieu truc tiep
    test_transactions = [
        ["A", "B", "C"],
        ["A", "B"],
        ["A", "C"],
        ["B", "C"],
        ["A", "B", "C"],
    ]

    print("=== TEST FP-GROWTH TREN DU LIEU NHO (5 giao dich) ===")
    for i, t in enumerate(test_transactions, 1):
        print(f"  T{i} = {{{', '.join(t)}}}")
    print()

    min_sup = 0.6
    print(f"min_support = {min_sup} (tuc >= {min_sup*5:.0f}/5 giao dich)")
    print()

    freq_itemsets, n = fp_growth(test_transactions, min_sup)
    print_results(freq_itemsets, n)

    print()
    print("=> Ket qua nay PHAI GIONG HET ket qua Apriori da chay truoc do")
    print("   (Frequent Itemsets la duy nhat cho 1 dataset + 1 min_support,")
    print("    khong phu thuoc thuat toan nao tao ra no)")
