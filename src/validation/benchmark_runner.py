"""Sequential, isolated benchmarks; never overwrite an earlier batch.

Quick check: python -B src/validation/benchmark_runner.py --smoke-test
Real data:  python -B src/validation/benchmark_runner.py --supports 0.05 --repeats 3

seconds measures ONLY the algorithm call, including its internal preparation and
result construction. Imports, JSON loading, input conversion, serialization and
validation are excluded. All algorithms receive sorted list-of-list baskets.
peak_rss_mib is sampled process RSS over the WHOLE worker lifetime (including
loading and serialization), not algorithm-only allocation or an exact OS peak.
Validation runs in the parent after the worker exits and is excluded from RSS.
Timeout bounds the whole worker lifetime, not just the algorithm call.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from itertools import combinations
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
import uuid

import psutil

from common import load_transactions, support_to_count

ROOT = Path(__file__).resolve().parents[2]
ALGORITHMS = ("apriori", "fpgrowth", "charm")


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2, allow_nan=False)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def git_info():
    def run(*args):
        proc = subprocess.run(["git", "-C", str(ROOT), *args],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=10)
        return proc.stdout.strip() if proc.returncode == 0 else None
    try:
        return {"commit": run("rev-parse", "HEAD"),
                "status": run("status", "--short"),
                "diff": run("diff", "--", "src")}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"unavailable": str(exc)}


def source_hashes():
    return {str(p.relative_to(ROOT)): file_hash(p)
            for p in sorted((ROOT / "src").rglob("*.py"))}


def read_itemsets(path):
    result = {}
    with Path(path).open(encoding="utf-8-sig") as file:
        for line in file:
            if not line.strip():
                continue
            row = json.loads(line)
            items = tuple(sorted(row["items"]))
            count = row["support_count"]
            if not items or len(items) != len(set(items)) or items in result:
                raise ValueError(f"Empty/duplicate itemset in {path}")
            if type(count) is not int or count < 1:
                raise ValueError(f"Invalid support count in {path}")
            result[items] = count
    return result


def expand_closed(closed):
    """support(X) = max support(C) over frequent closed supersets C of X."""
    frequent = {}
    for items, count in closed.items():
        for size in range(1, len(items) + 1):
            for subset in combinations(items, size):
                frequent[subset] = max(count, frequent.get(subset, 0))
    return frequent


def worker(args):
    # Imports and input conversion must finish before starting the common timer.
    sys.path.insert(0, str(ROOT / "src" / "algorithms"))
    if args.worker == "apriori":
        from apriori import apriori as mine
    elif args.worker == "fpgrowth":
        from fp_growth import fp_growth as mine
    else:
        from charm_impl import charm_mine as mine
    transactions, _, sha = load_transactions(args.data)
    if sha != args.expected_sha:
        raise ValueError("Dataset SHA256 changed before worker loading")
    baskets = [sorted(t) for t in transactions]
    del transactions
    min_count = support_to_count(args.support, len(baskets))
    start = time.perf_counter()
    if args.worker == "charm":
        mined, _ = mine(baskets, min_count)
    else:
        mined, _ = mine(baskets, float(args.support))
    seconds = time.perf_counter() - start
    if args.worker == "charm":
        rows = [{"items": list(r["items"]), "support_count": r["support_count"]}
                for r in mined]
    else:
        rows = [{"items": sorted(k), "support_count": v} for k, v in mined.items()]
    with (args.run_dir / "itemsets.jsonl").open("x", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row) + "\n")
    write_json(args.run_dir / "worker.json", {
        "seconds": seconds, "n_itemsets": len(rows), "min_count": min_count,
        "n_transactions": len(baskets), "data_sha256": sha, "pid": os.getpid(),
    })


def smoke_data(batch, supports):
    baskets = [["A", "B"], ["A", "B"], ["A", "B", "C"],
               ["A", "C"], ["C"], ["A", "B", "C"]]
    data = batch / "smoke_transactions.jsonl"
    with data.open("x", encoding="utf-8") as file:
        for i, basket in enumerate(baskets):
            file.write(json.dumps({"InvoiceNo": str(i), "items": basket}) + "\n")
    # Exhaustive tiny oracle; does not call any of the benchmarked algorithms.
    counts = Counter()
    for basket in baskets:
        for k in range(1, len(basket) + 1):
            counts.update(combinations(sorted(basket), k))
    references = batch / "smoke_references"
    references.mkdir()
    for support in supports:
        mc = support_to_count(support, len(baskets))
        frequent = {k: v for k, v in counts.items() if v >= mc}
        closed = {k: v for k, v in frequent.items()
                  if not any(set(k) < set(other) and v == count
                             for other, count in frequent.items())}
        path = references / f"closed_reference_{support.replace('.', '_')}.jsonl"
        with path.open("x", encoding="utf-8") as file:
            for items, count in sorted(closed.items()):
                file.write(json.dumps({"items": items, "support_count": count}) + "\n")
    return data, references


def prepare_references(args, sha, lf_sha, n):
    evidence = {}
    if not args.smoke_test:
        summary = args.reference_dir / "closed_check_summary.csv"
        with summary.open(encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                evidence[Decimal(row["min_support"])] = row
    refs, metadata = {}, {}
    for support in args.supports:
        path = args.reference_dir / f"closed_reference_{support.replace('.', '_')}.jsonl"
        closed = read_itemsets(path)
        mc = support_to_count(support, n)
        # Existing Apriori/FP-Growth APIs multiply floats internally. Refuse an
        # ambiguous boundary rather than silently benchmark a different cutoff.
        if math.ceil(float(support) * n) != mc:
            raise ValueError(f"Float/decimal threshold disagreement for {support}")
        if any(not mc <= count <= n for count in closed.values()):
            raise ValueError(f"Reference counts outside threshold: {path}")
        row = evidence.get(Decimal(support))
        if not args.smoke_test:
            if (not row or row["data_sha256"].lower() not in {sha, lf_sha}
                    or int(row["min_count"]) != mc
                    or int(row["closed"]) != len(closed)
                    or row["charm_matches_reference"].lower() != "true"):
                raise ValueError(f"Missing/inconsistent reference provenance for {support}")
        frequent = expand_closed(closed)
        if row and int(row["frequent"]) != len(frequent):
            raise ValueError(f"Reference frequent count inconsistent: {path}")
        refs[support] = (closed, frequent)
        metadata[support] = {"path": str(path), "sha256": file_hash(path),
                             "n_closed": len(closed), "n_frequent": len(frequent),
                             "summary_evidence": row}
    return refs, metadata


def run_one(args, batch, algorithm, support, repeat, sha, references):
    run_dir = batch / f"{repeat:02d}_{algorithm}_{support.replace('.', '_')}"
    run_dir.mkdir()
    record = {"algorithm": algorithm, "min_support": support, "repeat": repeat,
              "status": "error", "seconds": None, "peak_rss_mib": None,
              "rss_samples": 0, "sample_interval_ms": args.sample_ms,
              "timeout_seconds": args.timeout, "data_sha256": sha,
              "matches_reference": None, "n_itemsets": None,
              "min_count": support_to_count(support, args.n_transactions),
              "n_transactions": args.n_transactions, "started_utc": utc_now(),
              "run_dir": str(run_dir), "error": None, "exit_code": None}
    command = [sys.executable, "-B", str(Path(__file__).resolve()),
               "--worker", algorithm, "--data", str(args.data), "--support", support,
               "--run-dir", str(run_dir), "--expected-sha", sha]
    process = None
    peak = 0
    started = time.perf_counter()
    try:
        # File-backed logs avoid pipe-buffer deadlocks and preserve tracebacks.
        with (run_dir / "stdout.log").open("x", encoding="utf-8") as stdout, \
                (run_dir / "stderr.log").open("x", encoding="utf-8") as stderr:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr, cwd=ROOT)
            try:
                child = psutil.Process(process.pid)
            except psutil.NoSuchProcess:
                child = None
            while process.poll() is None:
                if child is not None:
                    try:
                        peak = max(peak, child.memory_info().rss)
                        record["rss_samples"] += 1
                    except psutil.NoSuchProcess:
                        child = None
                remaining = args.timeout - (time.perf_counter() - started)
                if remaining <= 0:
                    record["status"] = "timeout"
                    process.kill()
                    break
                time.sleep(min(args.sample_ms / 1000, remaining))
            process.wait()
        record["exit_code"] = process.returncode
        record["worker_wall_seconds"] = time.perf_counter() - started
        if record["status"] == "timeout":
            record["error"] = "Worker lifetime exceeded timeout; mining time is unknown"
        elif process.returncode:
            record["error"] = "Worker failed; see stderr.log"
        else:
            info = json.loads((run_dir / "worker.json").read_text(encoding="utf-8"))
            record.update(info)
            actual = read_itemsets(run_dir / "itemsets.jsonl")
            expected = references[0 if algorithm == "charm" else 1]
            matches = actual == expected
            record.update(status="ok" if matches else "mismatch", matches_reference=matches,
                          n_expected=len(expected), missing=len(expected.keys() - actual.keys()),
                          extra=len(actual.keys() - expected.keys()),
                          wrong_support=sum(actual[k] != expected[k]
                                            for k in actual.keys() & expected.keys()))
    except KeyboardInterrupt:
        record.update(status="error", error="Interrupted by user")
        raise
    except Exception as exc:
        record.update(status="error", error=f"{type(exc).__name__}: {exc}")
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        record["peak_rss_mib"] = peak / (1024 * 1024) if record["rss_samples"] else None
        record["finished_utc"] = utc_now()
        write_json(run_dir / "record.json", record)
        with (batch / "runs.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            file.flush()
            os.fsync(file.fileno())
    return record


def summarize(records, args, batch):
    summary = []
    for algorithm in args.algorithms:
        for support in args.supports:
            group = [r for r in records if r["algorithm"] == algorithm
                     and r["min_support"] == support]
            ok = [r for r in group if r["status"] == "ok"]
            row = {"algorithm": algorithm, "min_support": support,
                   "n_planned": args.repeats, "n_attempted": len(group),
                   "n_success": len(ok), "n_timeout": sum(r["status"] == "timeout" for r in group),
                   "n_error": sum(r["status"] == "error" for r in group),
                   "n_mismatch": sum(r["status"] == "mismatch" for r in group)}
            for metric in ("seconds", "peak_rss_mib"):
                values = [r[metric] for r in ok if r[metric] is not None]
                row.update({f"{metric}_n": len(values),
                            f"{metric}_mean": statistics.mean(values) if values else None,
                            f"{metric}_sample_sd": statistics.stdev(values) if len(values) > 1 else None,
                            f"{metric}_min": min(values) if values else None,
                            f"{metric}_max": max(values) if values else None})
            summary.append(row)
    with (batch / "summary.csv").open("x", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    write_json(batch / "summary.json", summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=ROOT / "data/processed/transactions_v1.jsonl")
    parser.add_argument("--reference-dir", type=Path, default=ROOT / "results")
    parser.add_argument("--output-root", type=Path, default=ROOT / "results/benchmarks")
    parser.add_argument("--supports", nargs="+")
    parser.add_argument("--algorithms", nargs="+", choices=ALGORITHMS, default=list(ALGORITHMS))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--sample-ms", type=float, default=50)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--worker", choices=ALGORITHMS, help=argparse.SUPPRESS)
    parser.add_argument("--support", help=argparse.SUPPRESS)
    parser.add_argument("--run-dir", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--expected-sha", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return 0
    if (args.repeats < 1 or not math.isfinite(args.timeout) or args.timeout <= 0
            or not math.isfinite(args.sample_ms) or args.sample_ms <= 0):
        parser.error("repeats, timeout and sample-ms must be positive and finite")
    args.supports = args.supports or (["0.5", "0.75"] if args.smoke_test else ["0.05"])
    try:
        supports = [Decimal(s) for s in args.supports]
        if any(not s.is_finite() or not 0 < s <= 1 for s in supports):
            raise ValueError("supports must be in (0, 1]")
        if len(set(supports)) != len(supports) or len(set(args.algorithms)) != len(args.algorithms):
            raise ValueError("duplicate supports/algorithms")
        args.supports = [format(s.normalize(), "f") for s in supports]
    except (InvalidOperation, ValueError) as exc:
        parser.error(str(exc))
    args.output_root = args.output_root.resolve()
    args.output_root.mkdir(parents=True, exist_ok=True)
    batch = args.output_root / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                                + "_" + uuid.uuid4().hex[:12])
    batch.mkdir()  # Exclusive: even a UUID collision must fail instead of overwrite.
    print(f"Batch: {batch}", flush=True)
    if args.smoke_test:
        args.data, args.reference_dir = smoke_data(batch, args.supports)
    args.data, args.reference_dir = args.data.resolve(), args.reference_dir.resolve()
    records = []
    try:
        raw = args.data.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        lf_sha = hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
        args.n_transactions = sum(bool(line.strip()) for line in raw.splitlines())
        del raw
        if not args.n_transactions:
            raise ValueError("Empty dataset")
        refs, ref_metadata = prepare_references(args, sha, lf_sha, args.n_transactions)
        hashes = source_hashes()
        write_json(batch / "metadata.json", {
            "created_utc": utc_now(), "smoke_test": args.smoke_test,
            "data_path": str(args.data), "data_sha256": sha, "data_lf_sha256": lf_sha,
            "n_transactions": args.n_transactions, "references": ref_metadata,
            "git": git_info(), "source_sha256": hashes,
            "environment": {"python": sys.version, "executable": sys.executable,
                            "platform": platform.platform(), "machine": platform.machine(),
                            "processor": platform.processor(), "logical_cpus": psutil.cpu_count(),
                            "physical_cpus": psutil.cpu_count(logical=False),
                            "total_ram_bytes": psutil.virtual_memory().total,
                            "psutil": psutil.__version__, "pythonhashseed": os.getenv("PYTHONHASHSEED")},
            "configuration": {"algorithms": args.algorithms, "supports": args.supports,
                              "repeats": args.repeats, "timeout_seconds": args.timeout,
                              "sample_interval_ms": args.sample_ms},
            "measurement": {"time": "perf_counter around algorithm call only",
                            "rss": "sampled worker-lifetime RSS; loading and serialization included",
                            "validation": "parent process after worker exit; exact itemsets and counts",
                            "frequent_reference": "all subsets of closed reference; maximum superset count",
                            "timeout": "whole worker lifetime; no numeric mining time for timeout",
                            "statistics": "verified ok runs only; sample SD uses n-1; null when n<2",
                            "order": "sequential; rotate algorithm order each repeat; no warmup"},
        })
        for repeat in range(1, args.repeats + 1):
            offset = (repeat - 1) % len(args.algorithms)
            order = args.algorithms[offset:] + args.algorithms[:offset]
            for support in args.supports:
                for algorithm in order:
                    if source_hashes() != hashes:
                        raise RuntimeError("Source changed during batch; stopping")
                    record = run_one(args, batch, algorithm, support, repeat, sha, refs[support])
                    records.append(record)
                    print(f"[{repeat}/{args.repeats}] {algorithm} support={support}: "
                          f"{record['status']} seconds={record['seconds']} "
                          f"sampled_peak_rss_mib={record['peak_rss_mib']}", flush=True)
        write_json(batch / "completion.json", {"status": "completed", "finished_utc": utc_now()})
    except (Exception, KeyboardInterrupt) as exc:
        write_json(batch / "batch_error.json", {"error": f"{type(exc).__name__}: {exc}",
                                               "finished_utc": utc_now()})
        print(f"Batch stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        # Also include the run whose finalizer persisted a record on Ctrl+C.
        if (batch / "runs.jsonl").exists():
            records = [json.loads(line) for line in
                       (batch / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
        if records:
            summarize(records, args, batch)
    return 0 if all(r["status"] == "ok" for r in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
