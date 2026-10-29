"""Recount exported rules from transactions, independently of CHARM support lookup.

Checks exported metrics at their documented rounding precision; does not prove
completeness of rule generation or validate product-family labels.
"""
import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

from common import load_transactions, support_to_count


def verify(transactions, rows, min_support, min_conf, min_lift):
    n = len(transactions)
    if not n:
        raise ValueError('Empty transaction database')
    index = defaultdict(set)
    for tid, basket in enumerate(transactions):
        for item in basket:
            index[item].add(tid)

    def tids(items):
        sets = [index[item] for item in items]
        return set.intersection(*sorted(sets, key=len)) if sets else set()

    seen = set()
    output = []
    for line, row in enumerate(rows, 2):
        result = dict(csv_line=line, antecedent=row.get('ma_ve_trai', ''),
                      consequent=row.get('ma_ve_phai', ''), count_x='', count_y='',
                      count_xy='', support_percent='', confidence_percent='', lift='',
                      passed=False, issues='')
        issues = []
        try:
            raw_x = row['ma_ve_trai'].split()
            x = frozenset(raw_x)
            y = frozenset(row['ma_ve_phai'].split())
            if not x or len(y) != 1 or x & y or len(raw_x) != len(x):
                raise ValueError('Invalid rule structure')
            if not 1 <= len(x) <= 3:
                issues.append('antecedent_outside_1_to_3')
            if (x, y) in seen:
                issues.append('duplicate_rule')
            seen.add((x, y))
            tx, ty = tids(x), tids(y)
            cx, cy, cxy = len(tx), len(ty), len(tx & ty)
            if not cx or not cy:
                raise ValueError('Zero support for antecedent or consequent')
            support = cxy / n
            conf = cxy / cx
            lift = conf / (cy / n)
            result.update(count_x=cx, count_y=cy, count_xy=cxy,
                          support_percent=support * 100,
                          confidence_percent=conf * 100, lift=lift)
            if int(row['so_giao_dich']) != cxy:
                issues.append('count_mismatch')
            if int(row['so_sp_ve_trai']) != len(x):
                issues.append('antecedent_size_mismatch')
            expected = {'support_%': round(support * 100, 2),
                        'confidence_%': round(conf * 100, 1), 'lift': round(lift, 1)}
            for key, value in expected.items():
                if not math.isclose(float(row[key]), value, rel_tol=0, abs_tol=1e-8):
                    issues.append(key + '_mismatch')
            if cxy < support_to_count(min_support, n) or conf < min_conf or lift < min_lift:
                issues.append('below_threshold')
        except (ValueError, KeyError, TypeError) as exc:
            issues.append(str(exc))
        result.update(passed=not issues, issues='; '.join(issues))
        output.append(result)
    return output


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data', default='data/processed/transactions_v1.jsonl')
    ap.add_argument('--rules', default='results/rules_0_005.csv')
    ap.add_argument('--out', default='results/rule_validation')
    ap.add_argument('--support', default='0.005')
    ap.add_argument('--min-conf', type=float, default=0.3)
    ap.add_argument('--min-lift', type=float, default=1.5)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if any((out / name).exists() for name in ['checked_rules.csv', 'summary.json']):
        ap.error('Output exists; choose a new --out directory')
    tx, _, sha = load_transactions(a.data)
    with Path(a.rules).open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    if not rows:
        ap.error('Rules file has no rules')
    checked = verify(tx, rows, a.support, a.min_conf, a.min_lift)
    with (out / 'checked_rules.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(checked[0]))
        writer.writeheader()
        writer.writerows(checked)
    passed = sum(r['passed'] for r in checked)
    summary = dict(n_transactions=len(tx), total_rules=len(checked), passed=passed,
                   failed=len(checked) - passed, data_sha256=sha,
                   rules_sha256=hashlib.sha256(Path(a.rules).read_bytes()).hexdigest(),
                   min_support=a.support, min_confidence=a.min_conf, min_lift=a.min_lift,
                   scope='Exported rule counts and rounded metrics; not completeness or family labels.')
    (out / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(f'Transactions: {len(tx)} | Rules: {len(checked)}')
    print(f'PASS: {passed} | FAIL: {len(checked) - passed}')
    print(f'Saved: {out}')
    raise SystemExit(0 if passed == len(checked) else 1)


if __name__ == '__main__':
    main()
