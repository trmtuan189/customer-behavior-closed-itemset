r"""Sinh luật kết hợp từ TẬP ĐÓNG (do CHARM khai thác) và xuất bảng phát hiện hành vi mua sắm.

Chạy từ thư mục gốc của repo:
  python src/analysis/make_rules.py --data data/processed/transactions_v1.jsonl --xlsx data/raw/online_retail_II.xlsx --out results

Đầu ra (trong thư mục --out):
  item_names.csv        bảng mã sản phẩm -> tên (cache, lần sau không cần đọc lại file xlsx)
  rules_<support>.csv   toàn bộ luật đạt ngưỡng (mở được bằng Excel)
  findings_<support>.csv và findings_<support>.md   10-15 phát hiện đa dạng để đưa vào báo cáo

Cách sinh luật: với mỗi tập đóng Z (2 đến max-ante+1 sản phẩm) và mỗi sản phẩm y trong Z, xét luật (Z - y) -> y.
Support của tập con X được lấy là support lớn nhất trong các tập đóng chứa X (không cần quét lại dữ liệu).
"""
from __future__ import annotations
import argparse, csv, re, sys, time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "validation"))
from common import load_transactions, support_to_count  # noqa: E402
from charm_impl import charm_mine  # noqa: E402

COLORS = set("red pink blue green yellow white black purple orange ivory cream silver gold grey gray brown "
             "turquoise lilac multi assorted".split())


def load_names(xlsx, cache):
    """Mã sản phẩm -> tên phổ biến nhất của mã đó."""
    if cache.exists():
        with cache.open(encoding="utf-8-sig") as f:
            return {r["code"]: r["name"] for r in csv.DictReader(f)}
    if not xlsx:
        return {}
    import pandas as pd
    x = pd.ExcelFile(xlsx, engine="openpyxl")
    df = pd.concat([pd.read_excel(x, s, usecols=["StockCode", "Description"]) for s in x.sheet_names])
    df["StockCode"] = df["StockCode"].astype("string").str.strip()
    df = df.dropna()
    names = df.groupby("StockCode")["Description"].agg(lambda s: s.value_counts().index[0]).to_dict()
    names = {str(k): re.sub(r"\s+", " ", str(v)).strip().title() for k, v in names.items()}
    with cache.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["code", "name"])
        w.writerows(sorted(names.items()))
    return names


def build_families(items, names):
    """Gộp các sản phẩm cùng dòng: cùng mã gốc, cùng tên khác màu, hoặc cùng 2 từ đầu của tên."""
    parent = {i: i for i in items}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    seen = {}
    for it in items:
        toks = [t for t in re.sub(r"[^a-z ]", " ", names.get(it, it).lower()).split() if t not in COLORS]
        keys = [("c", re.sub(r"[A-Za-z]+$", "", it)), ("n", " ".join(toks))]
        if len(toks) >= 3:
            keys.append(("p", " ".join(toks[:2])))
        for k in keys:
            if k in seen:
                parent[find(it)] = find(seen[k])
            else:
                seen[k] = it
    return {it: find(it) for it in items}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--xlsx", default=None, help="file online_retail_II.xlsx để lấy tên sản phẩm")
    ap.add_argument("--out", default="results")
    ap.add_argument("--support", default="0.005", help="min_support dạng chuỗi, ví dụ 0.005")
    ap.add_argument("--min-conf", type=float, default=0.3)
    ap.add_argument("--min-lift", type=float, default=1.5)
    ap.add_argument("--max-ante", type=int, default=3, help="số sản phẩm tối đa ở vế trái")
    ap.add_argument("--n-findings", type=int, default=15)
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tx, _, sha = load_transactions(a.data)
    n = len(tx)
    mc = support_to_count(a.support, n)
    tag = a.support.replace(".", "_")
    print(f"Dữ liệu: {n} giao dịch | SHA256 {sha[:12]}… | min_support {a.support} (min_count {mc})")

    t0 = time.perf_counter()
    rows, _ = charm_mine(tx, mc)
    closed = {frozenset(r["items"]): int(r["support_count"]) for r in rows}
    print(f"CHARM: {len(closed)} tập đóng ({time.perf_counter() - t0:.1f}s)")

    items = sorted({i for s in closed for i in s})
    names = load_names(a.xlsx, out / "item_names.csv")
    nm = lambda i: names.get(i, i)
    fam = build_families(items, names)

    sets = list(closed.items())
    index = defaultdict(set)
    for k, (s, _) in enumerate(sets):
        for it in s:
            index[it].add(k)

    def support(x):
        c = closed.get(x)
        if c is not None:
            return c
        ids = set.intersection(*(index[i] for i in sorted(x, key=lambda i: len(index[i]))))
        return max(sets[k][1] for k in ids)

    rules = []
    for z, cz in closed.items():
        if not 2 <= len(z) <= a.max_ante + 1:
            continue
        for y in z:
            x = z - {y}
            conf = cz / support(x)
            lift = conf / (support(frozenset([y])) / n)
            if conf >= a.min_conf and lift >= a.min_lift:
                rules.append({
                    "ante": tuple(sorted(x)), "cons": y, "count": cz, "support": cz / n,
                    "conf": conf, "lift": lift,
                    "same_family": fam[y] in {fam[i] for i in x},
                })
    rules.sort(key=lambda r: (-r["conf"], -r["lift"]))
    print(f"Luật đạt ngưỡng (conf >= {a.min_conf}, lift >= {a.min_lift}): {len(rules)} "
          f"| trong đó khác dòng sản phẩm: {sum(not r['same_family'] for r in rules)}")

    with (out / f"rules_{tag}.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["ve_trai", "ve_phai", "ma_ve_trai", "ma_ve_phai", "so_giao_dich", "support_%", "confidence_%",
                    "lift", "so_sp_ve_trai", "cung_dong_san_pham"])
        for r in rules:
            w.writerow([" + ".join(nm(i) for i in r["ante"]), nm(r["cons"]), " ".join(r["ante"]), r["cons"],
                        r["count"], round(r["support"] * 100, 2), round(r["conf"] * 100, 1), round(r["lift"], 1),
                        len(r["ante"]), "có" if r["same_family"] else "không"])

    # 10-15 phát hiện đa dạng: khác dòng sản phẩm, vế trái tối đa 2 sản phẩm, mỗi dòng sản phẩm chỉ xuất hiện một lần
    cand = [r for r in rules if not r["same_family"] and len(r["ante"]) <= 2 and r["conf"] >= 0.4]
    cand.sort(key=lambda r: (-r["count"], -r["lift"]))
    used, picks = set(), []
    for r in cand:
        fams = {fam[i] for i in r["ante"]} | {fam[r["cons"]]}
        if fams & used:
            continue
        picks.append(r)
        used |= fams
        if len(picks) == a.n_findings:
            break

    hdr = ["STT", "Nếu khách mua", "Thì thường mua thêm", "Số hoá đơn", "Support (%)", "Confidence (%)", "Lift",
           "Ý nghĩa kinh doanh / Đề xuất (tự điền)"]
    with (out / f"findings_{tag}.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(hdr)
        for k, r in enumerate(picks, 1):
            w.writerow([k, " + ".join(nm(i) for i in r["ante"]), nm(r["cons"]), r["count"],
                        round(r["support"] * 100, 2), round(r["conf"] * 100, 1), round(r["lift"], 1), ""])
    with (out / f"findings_{tag}.md").open("w", encoding="utf-8") as f:
        f.write("| " + " | ".join(hdr) + " |\n|" + "---|" * len(hdr) + "\n")
        for k, r in enumerate(picks, 1):
            f.write(f"| {k} | {' + '.join(nm(i) for i in r['ante'])} | {nm(r['cons'])} | {r['count']} | "
                    f"{r['support'] * 100:.2f} | {r['conf'] * 100:.1f} | {r['lift']:.1f} |  |\n")
    print(f"\nĐã lưu: rules_{tag}.csv, findings_{tag}.csv, findings_{tag}.md trong {out}\n")
    for k, r in enumerate(picks, 1):
        print(f"{k:>2}. {' + '.join(nm(i) for i in r['ante'])}  ->  {nm(r['cons'])}   "
              f"[{r['count']} hđ | conf {r['conf'] * 100:.0f}% | lift {r['lift']:.1f}]")


if __name__ == "__main__":
    main()
