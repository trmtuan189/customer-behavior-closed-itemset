r"""Chạy Apriori và FP-Growth CỦA NHÓM trên transactions_v1, đo thời gian, lưu kết quả và so khớp.

Ví dụ (trong thư mục src):
  python src/validation/run_baselines.py --data data/processed/transactions_v1.jsonl --algo-dir src/algorithms --out results --timeout 900

Mỗi lần chạy 1 thuật toán + 1 mức support nằm trong một tiến trình riêng, có giới hạn thời gian.
Nếu một mức bị quá thời gian thì các mức support thấp hơn của thuật toán đó được ghi là 'bỏ qua'.
Cần chạy check_closed.py trước để có sẵn file results\closed_reference_*.jsonl.
"""
from __future__ import annotations
import argparse, csv, json, subprocess, sys, time
from pathlib import Path
import psutil

from common import load_transactions, to_rows, save_rows, load_rows
from closed_filter import filter_closed

NAMES = {"apriori": "Apriori", "fpgrowth": "FP-Growth"}


def measure_memory(process, timeout):
    """Sample child-process RSS every 50 ms; return peak MiB and timeout flag."""
    peak_memory = 0
    started = time.perf_counter()
    timed_out = False
    try:
        child = psutil.Process(process.pid)
    except psutil.NoSuchProcess:
        child = None

    while process.poll() is None:
        if time.perf_counter() - started >= timeout:
            timed_out = True
            process.kill()
            break
        if child is not None:
            try:
                peak_memory = max(peak_memory, child.memory_info().rss)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        time.sleep(0.05)

    return round(peak_memory / (1024 * 1024), 2), timed_out


def worker(a):
    sys.path.append(a.algo_dir)
    tx, _, _ = load_transactions(a.data)
    baskets = [sorted(b) for b in tx]
    n = len(baskets)
    t0 = time.perf_counter()
    if a.algo == "apriori":
        from apriori import apriori
        freq, _ = apriori(baskets, float(a.support))
    else:
        from fp_growth import fp_growth
        freq, _ = fp_growth(baskets, float(a.support))
    sec = time.perf_counter() - t0
    save_rows(to_rows(freq, n), Path(a.out) / f"{a.algo}_{a.support.replace('.', '_')}.jsonl")
    print(json.dumps({"seconds": sec, "n_frequent": len(freq)}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", action="store_true")
    ap.add_argument("--algo", default=None)
    ap.add_argument("--support", default=None)
    ap.add_argument("--data", required=True)
    ap.add_argument("--algo-dir", required=True)
    ap.add_argument("--out", default="../results")
    ap.add_argument("--supports", nargs="+", default=["0.05", "0.03", "0.02", "0.01", "0.005"])
    ap.add_argument("--algos", nargs="+", default=["fpgrowth", "apriori"])
    ap.add_argument("--timeout", type=int, default=900, help="giây cho mỗi lần chạy")
    a = ap.parse_args()
    if a.worker:
        return worker(a)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for algo in a.algos:
        slow = False
        for s in a.supports:
            rec = {"algorithm": NAMES[algo], "min_support": s, "status": "", "seconds": "",
                   "peak_memory_mb": "", "n_frequent": "", "n_closed_from_frequent": "",
                   "matches_reference": ""}
            if slow:
                rec["status"] = "bỏ qua (mức cao hơn đã quá thời gian)"
                rows.append(rec); print(f"[{NAMES[algo]} {s}] {rec['status']}"); continue
            print(f"[{NAMES[algo]} {s}] đang chạy (tối đa {a.timeout}s)...", flush=True)
            cmd = [sys.executable, __file__, "--worker", "--algo", algo, "--support", s, "--data", a.data,
                   "--algo-dir", a.algo_dir, "--out", str(out)]
            try:
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                peak_mb, timed_out = measure_memory(p, a.timeout)
                stdout, stderr = p.communicate()
            except OSError as exc:
                rec["status"] = "lỗi"
                rows.append(rec); print(f"   => LỖI: {exc}"); continue
            if timed_out:
                rec["status"] = "timeout"; rec["seconds"] = f">{a.timeout}"; slow = True
                rec["peak_memory_mb"] = peak_mb
                rows.append(rec); print(f"   => timeout sau {a.timeout}s"); continue
            if p.returncode != 0:
                rec["status"] = "lỗi"; rows.append(rec)
                print("   => LỖI:\n", stderr[-1500:]); continue
            info = json.loads(stdout.strip().splitlines()[-1])
            freq = {frozenset(k): v for k, v in load_rows(out / f"{algo}_{s.replace('.', '_')}.jsonl").items()}
            closed = {tuple(sorted(k)): v for k, v in filter_closed(freq).items()}
            ref_path = out / f"closed_reference_{s.replace('.', '_')}.jsonl"
            match = (closed == load_rows(ref_path)) if ref_path.exists() else "chưa có file đối chứng"
            rec.update(status="ok", seconds=round(info["seconds"], 2), peak_memory_mb=peak_mb,
                       n_frequent=info["n_frequent"],
                       n_closed_from_frequent=len(closed), matches_reference=match)
            rows.append(rec)
            print(f"   => {rec['seconds']}s | RAM đỉnh (lấy mẫu): {peak_mb} MiB | {rec['n_frequent']} tập phổ biến -> {len(closed)} tập đóng | khớp đối chứng: {match}")

    with (out / "baseline_timing.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print("\nĐã lưu:", out / "baseline_timing.csv")


if __name__ == "__main__":
    main()
