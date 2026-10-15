"""CHARM do nhóm cài đặt (từ notebook, mục 6) để đối chiếu.
ĐÃ SỬA 1 lỗi: khi gặp Property 1/2 mà new_class còn phần tử, mã gốc giải nén ITNode như tuple
(for items, tids in new_class) và báo TypeError. Nay dùng node.items / node.tidset."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from time import perf_counter
from pathlib import Path
from decimal import Decimal, ROUND_CEILING
import json
import pandas as pd


@dataclass(frozen=True)
class ITNode:
    items: frozenset
    tidset: frozenset


class CharmTimeout(RuntimeError):
    pass


def build_vertical_db(transactions):
    index = defaultdict(set)

    for tid, basket in enumerate(transactions):
        for item in set(basket):
            index[item].add(tid)

    return {
        item: frozenset(tids)
        for item, tids in index.items()
    }


def _check_deadline(deadline, timeout_seconds):
    if deadline is not None and perf_counter() > deadline:
        raise CharmTimeout(
            f"CHARM vượt quá giới hạn {timeout_seconds} giây."
        )


def charm_mine(transactions, min_support_count, timeout_seconds=None):
    # CHARM-style mining trên IT-tree:
    # - depth-first prefix classes
    # - support bằng tidset intersection
    # - 4 properties để gộp/prune
    # - explicit closure guard ở cuối

    if min_support_count < 1:
        raise ValueError("min_support_count phải >= 1")

    vertical = build_vertical_db(transactions)

    candidates = [
        ITNode(frozenset([item]), tids)
        for item, tids in vertical.items()
        if len(tids) >= min_support_count
    ]

    # Sort tăng dần support để tăng cơ hội gặp Property 1/2.
    candidates.sort(
        key=lambda node: (len(node.tidset), tuple(sorted(node.items)))
    )

    stats = {
        "intersections": 0,
        "frequent_extensions": 0,
        "pruned_infrequent": 0,
        "property_equal": 0,
        "property_subset": 0,
        "property_superset": 0,
        "property_unequal": 0,
        "nodes_processed": 0,
        "terminal_candidates": 0,
        "timeout_seconds": timeout_seconds,
    }

    terminals = []
    deadline = (
        perf_counter() + timeout_seconds
        if timeout_seconds is not None
        else None
    )

    transaction_sets = [set(basket) for basket in transactions]

    def extend(class_nodes):
        nodes = list(class_nodes)
        i = 0

        while i < len(nodes):
            _check_deadline(deadline, timeout_seconds)

            Xi = nodes[i].items
            Ti = nodes[i].tidset
            X = Xi
            new_class = []

            j = i + 1

            while j < len(nodes):
                if (stats["intersections"] & 255) == 0:
                    _check_deadline(deadline, timeout_seconds)

                Xj = nodes[j].items
                Tj = nodes[j].tidset
                Y = Ti & Tj
                stats["intersections"] += 1

                if len(Y) < min_support_count:
                    stats["pruned_infrequent"] += 1
                    j += 1
                    continue

                stats["frequent_extensions"] += 1
                X_union = X | Xj

                if Ti == Tj:
                    # Property 1
                    stats["property_equal"] += 1
                    nodes.pop(j)
                    X = X_union
                    new_class = [
                        ITNode(node.items | Xj, node.tidset)
                        for node in new_class
                    ]
                    continue

                if Ti < Tj:
                    # Property 2
                    stats["property_subset"] += 1
                    X = X_union
                    new_class = [
                        ITNode(node.items | Xj, node.tidset)
                        for node in new_class
                    ]
                    j += 1
                    continue

                if Ti > Tj:
                    # Property 3
                    stats["property_superset"] += 1
                    nodes.pop(j)
                    new_class.append(
                        ITNode(X_union, Y)
                    )
                    continue

                # Property 4
                stats["property_unequal"] += 1
                new_class.append(
                    ITNode(X_union, Y)
                )
                j += 1

            stats["nodes_processed"] += 1

            new_class.sort(
                key=lambda node: (
                    len(node.tidset),
                    tuple(sorted(node.items)),
                )
            )

            if new_class:
                extend(new_class)

            terminals.append(ITNode(X, Ti))
            stats["terminal_candidates"] += 1
            i += 1

    extend(candidates)

    # Correctness guard:
    # closure(X) = giao các item xuất hiện trong toàn bộ tids của X.
    closed = {}

    for node in terminals:
        T = node.tidset
        if not T:
            continue

        first_tid = next(iter(T))
        closure = set(transaction_sets[first_tid])

        for tid in T:
            closure.intersection_update(transaction_sets[tid])

        closure = frozenset(closure)

        if closure == node.items:
            closed[(closure, T)] = True

    result = [
        {
            "items": tuple(sorted(items)),
            "support_count": len(tidset),
            "support": len(tidset) / len(transactions),
        }
        for items, tidset in closed.keys()
    ]

    result.sort(
        key=lambda row: (
            row["support_count"],
            len(row["items"]),
            row["items"],
        ),
        reverse=True,
    )

    return result, stats
