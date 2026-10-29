"""Sequential, isolated benchmark. Run from the repository root.

Timing excludes imports, input loading, verification and output writing.
Windows peak working set is for the whole worker up to mining completion,
including imports and input loading; it is not incremental algorithm memory.
"""
import argparse
import csv
import ctypes
from ctypes import wintypes
from fractions import Fraction
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

from common import load_transactions, support_to_count


def peak_memory_mb():
    if sys.platform != 'win32':
        return None

    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    info = Counters()
    info.cb = ctypes.sizeof(info)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(info), info.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return info.PeakWorkingSetSize / 1024 ** 2


def worker(a):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'algorithms'))
    if a.algo == 'CHARM':
        from charm_impl import charm_mine
    elif a.algo == 'Apriori':
        from apriori import apriori
    else:
        from fp_growth import fp_growth
    tx, _, sha = load_transactions(a.data)
    # All algorithms receive the same ordered, duplicate-free baskets.
    baskets = [sorted(t) for t in tx]
    mc = support_to_count(a.support, len(tx))
    start = time.perf_counter()
    if a.algo == 'CHARM':
        rows, _ = charm_mine(baskets, mc)
        mined = {frozenset(r['items']): r['support_count'] for r in rows}
    elif a.algo == 'Apriori':
        mined, _ = apriori(baskets, Fraction(mc, len(tx)))
    else:
        mined, _ = fp_growth(baskets, float(a.support), min_count=mc)
    seconds = time.perf_counter() - start
    peak = peak_memory_mb()
    # Verification runs after both timing and memory capture.
    from reference_miner import mine_frequent
    from closed_filter import filter_closed
    reference = mine_frequent(tx, mc)
    expected = filter_closed(reference) if a.algo == 'CHARM' else reference
    print(json.dumps(dict(seconds=seconds, peak_process_mb=peak,
                          n_itemsets=len(mined), matches_reference=mined == expected,
                          min_count=mc, n_transactions=len(tx), data_sha256=sha)))


def write_csv(path, rows):
    with path.open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data', default='data/processed/transactions_v1.jsonl')
    ap.add_argument('--out', default='results/benchmark_all')
    ap.add_argument('--supports', nargs='+', default=['0.05', '0.03', '0.02'])
    ap.add_argument('--repeats', type=int, default=3)
    ap.add_argument('--timeout', type=int, default=900)
    ap.add_argument('--worker', action='store_true')
    ap.add_argument('--algo', choices=['CHARM', 'FP-Growth', 'Apriori'])
    ap.add_argument('--support')
    a = ap.parse_args()
    if a.repeats < 1 or a.timeout < 1:
        ap.error('repeats and timeout must be positive')
    if a.worker:
        return worker(a)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    raw_path = out / 'runs.csv'
    summary_path = out / 'summary.csv'
    if raw_path.exists() or summary_path.exists():
        ap.error('Output already exists. Choose another --out directory.')
    metadata = dict(python=sys.version, platform=platform.platform(),
                    processor=platform.processor(), supports=a.supports,
                    repeats=a.repeats, timeout=a.timeout,
                    memory_scope='Worker lifetime peak working set before verification; includes imports and input.',
                    timing_scope='Mining call and output normalization only; excludes imports, input and verification.')
    revision = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True)
    metadata['git_revision'] = revision.stdout.strip()
    (out / 'environment.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    runs, summaries = [], []
    failed = False
    for support in a.supports:
        for algo in ['CHARM', 'FP-Growth', 'Apriori']:
            completed = []
            for repeat in range(1, a.repeats + 1):
                print(f'{algo} | support={support} | run {repeat}/{a.repeats}', flush=True)
                rec = dict(algorithm=algo, min_support=support, repeat=repeat,
                           status='', seconds='', peak_process_mb='', n_itemsets='',
                           matches_reference='', min_count='', n_transactions='', data_sha256='', error='')
                cmd = [sys.executable, str(Path(__file__).resolve()), '--worker',
                       '--data', a.data, '--algo', algo, '--support', support]
                try:
                    p = subprocess.run(cmd, capture_output=True, text=True, timeout=a.timeout)
                    if p.returncode:
                        raise RuntimeError(p.stderr[-1500:])
                    info = json.loads(p.stdout.strip().splitlines()[-1])
                    rec.update(info, status='ok' if info['matches_reference'] else 'mismatch')
                    if info['matches_reference']:
                        completed.append(info)
                    print(f"  {info['seconds']:.3f}s | peak={info['peak_process_mb']} MB | match={info['matches_reference']}", flush=True)
                except subprocess.TimeoutExpired:
                    rec.update(status='timeout', error=f'Worker exceeded {a.timeout}s including setup and verification')
                except Exception as exc:
                    rec.update(status='error', error=str(exc))
                runs.append(rec)
                write_csv(raw_path, runs)
                if rec['status'] != 'ok':
                    print(f"  {rec['status']}: {rec['error']}", flush=True)
                    failed = True
                    break
            summaries.append(dict(algorithm=algo, min_support=support,
                                  valid_runs=len(completed), requested_runs=a.repeats,
                                  median_seconds=statistics.median(x['seconds'] for x in completed) if completed else '',
                                  median_peak_process_mb=statistics.median(x['peak_process_mb'] for x in completed) if completed and all(x['peak_process_mb'] is not None for x in completed) else '',
                                  n_itemsets=completed[0]['n_itemsets'] if completed else ''))
            write_csv(summary_path, summaries)
    print(f'Saved: {summary_path}', flush=True)
    if failed:
        print('Some configurations failed or were incomplete. Check runs.csv before reporting.')
        sys.exit(1)


if __name__ == '__main__':
    main()
