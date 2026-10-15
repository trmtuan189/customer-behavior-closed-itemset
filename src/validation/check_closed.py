"""Đối chiếu CHARM với đáp án đối chứng (tập phổ biến tham chiếu -> lọc closed).

Chạy:
    python check_closed.py --data ../data/transactions_v1.jsonl --supports 0.05 0.03 0.02 0.01 0.005
Tuỳ chọn:
    --charm-dir <thư mục chứa closed_itemsets_0_01.jsonl ...>  # so với kết quả CHARM đã lưu
"""
from __future__ import annotations
import argparse
import csv
import time
from pathlib import Path

from common import load_transactions, support_to_count, to_rows, save_rows, load_rows
from reference_miner import mine_frequent
from closed_filter import filter_closed
from charm_impl import charm_mine


def diff_report(name_a, a, name_b, b, limit=5):
    only_a = set(a) - set(b)
    only_b = set(b) - set(a)
    diff_sup = [k for k in set(a) & set(b) if a[k] != b[k]]
    print(f"   {name_a}: {len(a)} | {name_b}: {len(b)} | chỉ có ở {name_a}: {len(only_a)}"
          f" | chỉ có ở {name_b}: {len(only_b)} | lệch support: {len(diff_sup)}")
    for k in list(only_a)[:limit]:
        print(f"      chỉ {name_a}: {k} (sup={a[k]})")
    for k in list(only_b)[:limit]:
        print(f"      chỉ {name_b}: {k} (sup={b[k]})")
    return not (only_a or only_b or diff_sup)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--supports", nargs="+", default=["0.05", "0.03", "0.02", "0.01", "0.005"])
    ap.add_argument("--out", default="../results")
    ap.add_argument("--charm-dir", default=None)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tx, invoices, sha = load_transactions(args.data)
    n = len(tx)
    print(f"Dữ liệu: {n} giao dịch | SHA256 = {sha}")

    summary = []
    all_ok = True
    for s in args.supports:
        mc = support_to_count(s, n)
        print(f"\n=== min_support = {s} (min_count = {mc}) ===")

        t0 = time.perf_counter()
        freq = mine_frequent(tx, mc)
        t_ref = time.perf_counter() - t0
        closed_ref = filter_closed(freq)
        print(f"   Tham chiếu: {len(freq)} tập phổ biến -> {len(closed_ref)} tập đóng ({t_ref:.1f}s)")
        ref = {tuple(sorted(k)): v for k, v in closed_ref.items()}
        save_rows(to_rows(closed_ref, n), out / f"closed_reference_{s.replace('.', '_')}.jsonl")

        t0 = time.perf_counter()
        charm_rows, _ = charm_mine(tx, mc)
        t_charm = time.perf_counter() - t0
        charm = {tuple(sorted(r["items"])): r["support_count"] for r in charm_rows}
        ok = diff_report("CHARM", charm, "THAM CHIẾU", ref)
        print("   => KHỚP 100%" if ok else "   => CÓ KHÁC BIỆT, cần kiểm tra")
        all_ok &= ok

        saved_ok = ""
        if args.charm_dir:
            p = Path(args.charm_dir) / f"closed_itemsets_{s.replace('.', '_')}.jsonl"
            if p.exists():
                saved = load_rows(p)
                saved_ok = diff_report("CHARM(file)", saved, "THAM CHIẾU", ref)
                all_ok &= saved_ok
            else:
                print(f"   (không thấy {p})")

        summary.append({
            "min_support": s, "min_count": mc,
            "frequent": len(freq), "closed": len(closed_ref),
            "compression_ratio": round(len(freq) / len(closed_ref), 3),
            "charm_matches_reference": ok, "saved_file_matches": saved_ok,
            "t_reference_s": round(t_ref, 2), "t_charm_s": round(t_charm, 2),
            "data_sha256": sha,
        })

    with (out / "closed_check_summary.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0]))
        w.writeheader()
        w.writerows(summary)
    print("\nTỔNG KẾT:", "TẤT CẢ KHỚP" if all_ok else "CÓ MỨC KHÔNG KHỚP")
    print("Đã lưu:", out / "closed_check_summary.csv")


if __name__ == "__main__":
    main()
