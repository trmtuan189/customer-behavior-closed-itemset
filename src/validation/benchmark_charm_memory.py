"""Benchmark CHARM time and sampled peak RSS on Windows.

Place this file in src/validation/ alongside common.py and charm_impl.py.
Run from repository root, for example:
    python src/validation/benchmark_charm_memory.py --data data/processed/transactions_v1.jsonl --out results --supports 0.05 --timeout 180
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

import psutil

from common import load_rows, load_transactions, support_to_count
from charm_impl import charm_mine


def worker(args):
    transactions, _, data_sha256 = load_transactions(args.data)
    min_count = support_to_count(args.support, len(transactions))

    start = time.perf_counter()
    closed_rows, _ = charm_mine(transactions, min_count)
    seconds = time.perf_counter() - start

    produced = {
        tuple(sorted(row["items"])): int(row["support_count"])
        for row in closed_rows
    }
    reference = Path(args.out) / f"closed_reference_{args.support.replace('.', '_')}.jsonl"
    matches_reference = produced == load_rows(reference) if reference.exists() else None

    print(json.dumps({
        "seconds": round(seconds, 2),
        "n_closed": len(closed_rows),
        "min_count": min_count,
        "matches_reference": matches_reference,
        "data_sha256": data_sha256,
    }))


def sample_memory(process, timeout):
    """Measure the child process's sampled peak RSS in MiB (50 ms polling)."""
    maximum = 0
    started = time.perf_counter()
    timed_out = False
    child = psutil.Process(process.pid)

    while process.poll() is None:
        if time.perf_counter() - started >= timeout:
            timed_out = True
            process.kill()
            break
        try:
            maximum = max(maximum, child.memory_info().rss)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        time.sleep(0.05)

    return round(maximum / (1024 * 1024), 2), timed_out


def main():
    parser = argparse.ArgumentParser(description="Measure CHARM computation time and peak RSS")
    parser.add_argument("--data", required=True)
    parser.add_argument("--out", default="results")
    parser.add_argument("--supports", nargs="+", default=["0.05", "0.03", "0.02", "0.01", "0.005"])
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--support")
    args = parser.parse_args()

    if args.worker:
        if not args.support:
            parser.error("--worker requires --support")
        return worker(args)

    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for support in args.supports:
        print(f"[CHARM {support}] đang chạy (tối đa {args.timeout}s)...", flush=True)
        command = [sys.executable, __file__, "--worker", "--data", args.data,
                   "--out", str(output), "--support", support]
        process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        peak, timed_out = sample_memory(process, args.timeout)
        stdout, stderr = process.communicate()
        record = {"algorithm": "CHARM", "min_support": support,
                  "status": "", "seconds": "", "peak_memory_mb": peak,
                  "n_closed": "", "min_count": "", "matches_reference": "",
                  "data_sha256": ""}
        if timed_out:
            record["status"] = "timeout"
            record["seconds"] = f">{args.timeout}"
            print(f"   => timeout sau {args.timeout}s")
        elif process.returncode != 0:
            record["status"] = "lỗi"
            print(f"   => LỖI:\n{stderr[-1500:]}")
        else:
            info = json.loads(stdout.strip().splitlines()[-1])
            record.update(status="ok", **info)
            print(f"   => {record['seconds']}s | RAM đỉnh (lấy mẫu): {peak} MiB | "
                  f"{record['n_closed']} tập đóng | khớp đối chứng: {record['matches_reference']}")
        records.append(record)

    destination = output / "charm_memory.csv"
    with destination.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    print("Đã lưu:", destination)


if __name__ == "__main__":
    main()
