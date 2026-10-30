"""Complete association rules from a COMPLETE family of frequent closed itemsets.

Every frequent nonempty X has a frequent closure C with count(X)=count(C).
Consequently count(X)=max(count(C): C closed, X subset C). Enumerating all
nonempty subsets of all closed sets recovers every frequent itemset. For each
frequent union U, every nonempty proper subset X gives exactly one X -> U-X.
No maximum antecedent/consequent size is imposed. Resource caps abort, never
silently truncate. Completeness requires complete, correct closed input.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
from itertools import combinations
import json
import statistics

RULE_FIELDS = ('schema_version','rule_id','antecedents','consequents','support_count',
               'antecedent_support_count','consequent_support_count','n_transactions',
               'support','confidence','lift','balanced_score','min_support',
               'min_confidence','min_lift','source_dataset_hash')


class ResourceLimitError(ValueError):
    """Requested exhaustive computation exceeds an explicit safety budget."""


def decimal_text(value):
    try:
        d = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"Invalid numeric threshold: {value}") from exc
    if not d.is_finite():
        raise ValueError("Thresholds must be finite")
    return format(d.normalize(), 'f')


@dataclass(frozen=True)
class Thresholds:
    min_support: str = '0.005'
    min_confidence: str = '0.5'
    min_lift: str = '1'

    def __post_init__(self):
        for name in ('min_support', 'min_confidence', 'min_lift'):
            object.__setattr__(self, name, decimal_text(getattr(self, name)))
        if not 0 < Decimal(self.min_support) <= 1:
            raise ValueError('min_support must be in (0, 1]')
        if not 0 <= Decimal(self.min_confidence) <= 1:
            raise ValueError('min_confidence must be in [0, 1]')
        if Decimal(self.min_lift) < 0:
            raise ValueError('min_lift must be nonnegative')

    def min_count(self, n):
        value = Fraction(self.min_support) * n
        return (value.numerator + value.denominator - 1) // value.denominator


def valid_n(n):
    if type(n) is not int or n <= 0:
        raise ValueError('n_transactions must be a positive integer')


def normalize_counts(itemsets, n):
    valid_n(n)
    normalized = {}
    for items, count in itemsets.items():
        key = frozenset(items)
        if not key or any(not isinstance(x, str) or not x for x in key):
            raise ValueError('Itemsets must contain nonempty StockCode strings')
        if len(key) != len(items) or key in normalized:
            raise ValueError('Duplicate item or itemset')
        if type(count) is not int or not 1 <= count <= n:
            raise ValueError('Support counts must be integers in [1, N]')
        normalized[key] = count
    return normalized


def recover_frequent(closed, n, thresholds, *, max_frequent=100000,
                     max_subset_visits=500000):
    """Recover exact supports, under the complete-closed-input precondition."""
    closed = normalize_counts(closed, n)
    if min(max_frequent, max_subset_visits) < 1:
        raise ValueError('Resource limits must be positive')
    if any(c < thresholds.min_count(n) for c in closed.values()):
        raise ValueError('Closed input contains an itemset below min_support')
    if sum((1 << len(k))-1 for k in closed) > max_subset_visits:
        raise ResourceLimitError('Closed subset visits exceed max_subset_visits')
    frequent = {}
    for items, count in closed.items():
        ordered = sorted(items)
        for size in range(1, len(ordered)+1):
            for subset in combinations(ordered, size):
                key = frozenset(subset)
                frequent[key] = max(count, frequent.get(key, 0))
                if len(frequent) > max_frequent:
                    raise ResourceLimitError('Recovered itemsets exceed max_frequent')
    # Reject an input that labels a nonclosed set as closed, or contradicts
    # antimonotonicity. Completeness still needs independent validation.
    nonclosed = set()
    for items, count in frequent.items():
        if len(items) > 1:
            for item in items:
                sub = items-{item}
                if frequent[sub] == count:
                    nonclosed.add(sub)
    if any(frequent[k] != c or k in nonclosed for k,c in closed.items()):
        raise ValueError('Inconsistent counts or input includes nonclosed itemsets')
    return frequent


def distribution(values):
    if not values:
        return {'n': 0, 'min': None, 'max': None, 'mean': None,
                'median': None, 'sample_sd': None, 'p25': None, 'p75': None, 'p95': None}
    ordered = sorted(values)
    def quantile(p):
        position = (len(ordered)-1)*p
        lo = int(position)
        hi = min(lo+1, len(ordered)-1)
        return ordered[lo]+(ordered[hi]-ordered[lo])*(position-lo)
    return {'n': len(values), 'min': ordered[0], 'max': ordered[-1],
            'mean': statistics.mean(values), 'median': statistics.median(values),
            'sample_sd': statistics.stdev(values) if len(values)>1 else None,
            'p25': quantile(.25), 'p75': quantile(.75), 'p95': quantile(.95)}


def rule_id(left, right, dataset_hash):
    raw = json.dumps({'dataset': dataset_hash, 'antecedents': sorted(left),
                      'consequents': sorted(right)}, sort_keys=True,
                     separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return 'ar_'+hashlib.sha256(raw).hexdigest()


def generate_rules(frequent, n, thresholds, dataset_hash, *, max_candidates=250000):
    """Return ALL passing directed rules and counts before/after each filter.

    Threshold decisions and ranking use exact integer/rational arithmetic.
    Floats appear only in exported metrics and descriptive distributions.
    n_pass_lift is independent across ALL candidates. The sequential lift pass
    after confidence equals n_final. Top-K is applied separately, never here.
    """
    frequent = normalize_counts(frequent, n)
    if max_candidates < 1:
        raise ValueError('max_candidates must be positive')
    if any(c < thresholds.min_count(n) for c in frequent.values()):
        raise ValueError('Frequent input contains an itemset below min_support')
    count = sum((1 << len(k))-2 for k in frequent)
    if count > max_candidates:
        raise ResourceLimitError(f'{count} rule candidates exceed {max_candidates}')
    for items, c in frequent.items():
        if len(items)>1:
            for x in items:
                sub = items-{x}
                if sub not in frequent or frequent[sub] < c:
                    raise ValueError('Frequent input must be downward complete with consistent counts')
    min_conf, min_lift = Fraction(thresholds.min_confidence), Fraction(thresholds.min_lift)
    rules, confidences, lifts = [], [], []
    n_conf = n_lift = 0
    shapes_all, shapes_final = Counter(), Counter()
    for union, cn in sorted(frequent.items(), key=lambda kv: tuple(sorted(kv[0]))):
        ordered = sorted(union)
        for size in range(1,len(ordered)):
            for left_tuple in combinations(ordered,size):
                left = frozenset(left_tuple)
                right = union-left
                cx, cy = frequent[left], frequent[right]
                confidence, lift = Fraction(cn,cx), Fraction(cn*n,cx*cy)
                support = Fraction(cn,n)
                confidences.append(float(confidence))
                lifts.append(float(lift))
                shape = f'{len(left)}->{len(right)}'
                shapes_all[shape] += 1
                pass_conf, pass_lift = confidence>=min_conf, lift>=min_lift
                n_conf += pass_conf
                n_lift += pass_lift
                if not (pass_conf and pass_lift):
                    continue
                shapes_final[shape] += 1
                balanced = support*confidence*max(Fraction(0),1-1/lift)
                rules.append({'schema_version': '1.0',
                    'rule_id': rule_id(left,right,dataset_hash),
                    'antecedents': sorted(left), 'consequents': sorted(right),
                    'support_count': cn, 'antecedent_support_count': cx,
                    'consequent_support_count': cy, 'n_transactions': n,
                    'support': float(support), 'confidence': float(confidence),
                    'lift': float(lift), 'balanced_score': float(balanced),
                    'min_support': thresholds.min_support,
                    'min_confidence': thresholds.min_confidence, 'min_lift': thresholds.min_lift,
                    'source_dataset_hash': dataset_hash})
    assert len(confidences)==count
    summary = {'n_frequent': len(frequent), 'n_candidates': count,
               'n_pass_confidence': n_conf, 'n_pass_lift_independent': n_lift,
               'n_pass_lift_after_confidence': len(rules), 'n_final': len(rules),
               'n_rejected_confidence': count-n_conf,
               'n_rejected_lift_after_confidence': n_conf-len(rules),
               'candidate_confidence': distribution(confidences),
               'candidate_lift': distribution(lifts),
               'final_confidence': distribution([r['confidence'] for r in rules]),
               'final_lift': distribution([r['lift'] for r in rules]),
               'candidate_shapes': dict(sorted(shapes_all.items())),
               'final_shapes': dict(sorted(shapes_final.items()))}
    return rules, summary


def ranking_key(rule, criterion):
    """Exact descending metric keys, then lexicographic StockCodes (ascending)."""
    s = Fraction(rule['support_count'],rule['n_transactions'])
    c = Fraction(rule['support_count'],rule['antecedent_support_count'])
    l = Fraction(rule['support_count']*rule['n_transactions'],
                 rule['antecedent_support_count']*rule['consequent_support_count'])
    balanced = s*c*max(Fraction(0),1-1/l)
    metrics = {'confidence': (c,l,s), 'lift': (l,c,s), 'support': (s,c,l),
               'balanced': (balanced,l,c,s)}
    if criterion not in metrics:
        raise ValueError(f'Unknown ranking: {criterion}')
    return tuple(-v for v in metrics[criterion])+(tuple(rule['antecedents']),tuple(rule['consequents']))


def top_rules(rules, criterion='balanced', k=20):
    if type(k) is not int or k<1:
        raise ValueError('Top-K must be a positive integer')
    return sorted(rules,key=lambda r:ranking_key(r,criterion))[:k]
