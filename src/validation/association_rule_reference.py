"""Independent test/validation oracles; never call the production rule generator."""
from decimal import Decimal
from itertools import combinations
import math


def brute_force_itemsets(transactions, min_support):
    """Tiny datasets only: enumerate the full product universe, scan every basket."""
    products = sorted(set().union(*(set(t) for t in transactions)))
    if len(products)>12:
        raise ValueError('Exhaustive tiny oracle supports at most 12 products')
    n = len(transactions)
    if not n:
        raise ValueError('Empty oracle dataset')
    frequent = {}
    for size in range(1,len(products)+1):
        for items in combinations(products,size):
            key = frozenset(items)
            count = sum(all(item in basket for item in items) for basket in transactions)
            if Decimal(count)>=Decimal(str(min_support))*n:
                frequent[key] = count
    closed = {x:cx for x,cx in frequent.items()
              if not any(x<y and cx==cy for y,cy in frequent.items())}
    return frequent, closed


def reference_rules(frequent, n, min_confidence, min_lift):
    """Independent binary-mask splitting and probability-based metrics.

    Returns every accepted rule plus independent filter tallies. Counts are
    integer exact; threshold decisions use Decimal integer cross-products.
    """
    expected = {}
    counts = {'n_candidates':0,'n_pass_confidence':0,'n_pass_lift_independent':0,'n_final':0}
    for union, count in frequent.items():
        items = sorted(union)
        for mask in range(1,(1<<len(items))-1):
            left = tuple(x for i,x in enumerate(items) if mask&(1<<i))
            right = tuple(x for i,x in enumerate(items) if not mask&(1<<i))
            cx, cy = frequent[frozenset(left)], frequent[frozenset(right)]
            counts['n_candidates'] += 1
            conf_ok = Decimal(count)>=Decimal(str(min_confidence))*cx
            lift_ok = Decimal(count*n)>=Decimal(str(min_lift))*cx*cy
            counts['n_pass_confidence'] += conf_ok
            counts['n_pass_lift_independent'] += lift_ok
            if conf_ok and lift_ok:
                counts['n_final'] += 1
                px,py,p_union = cx/n, cy/n, count/n
                expected[left,right] = {'support_count':count,
                    'antecedent_support_count':cx,'consequent_support_count':cy,
                    'support':p_union,'confidence':p_union/px,'lift':p_union/(px*py)}
    return expected, counts


def compare_rules(actual, expected, *, tolerance=1e-12):
    keyed = {(tuple(r['antecedents']),tuple(r['consequents'])):r for r in actual}
    if len(keyed)!=len(actual):
        raise AssertionError('Duplicate rule sides')
    if keyed.keys()!=expected.keys():
        raise AssertionError(f'Rule set mismatch: extra={len(keyed.keys()-expected.keys())}, '
                             f'missing={len(expected.keys()-keyed.keys())}')
    for key, ref in expected.items():
        row = keyed[key]
        for name in ('support_count','antecedent_support_count','consequent_support_count'):
            if row[name]!=ref[name]:
                raise AssertionError(f'Count mismatch: {key} {name}')
        for name in ('support','confidence','lift'):
            if not math.isclose(row[name],ref[name],rel_tol=tolerance,abs_tol=tolerance):
                raise AssertionError(f'Metric mismatch: {key} {name}')
    return {'rules_checked':len(keyed),'exact_rule_keys':True,'exact_counts':True,
            'metrics_tolerance':tolerance,'passed':True}


def build_vertical_masks(transactions):
    """Independent actual-data support recount using integer bit masks."""
    masks = {}
    for tid,basket in enumerate(transactions):
        bit = 1<<tid
        for item in basket:
            masks[item] = masks.get(item,0)|bit
    return masks


def recount_itemsets(frequent, masks):
    for items,expected_count in frequent.items():
        ordered = sorted(items,key=lambda item:masks[item].bit_count())
        tids = masks[ordered[0]]
        for item in ordered[1:]:
            tids &= masks[item]
        actual = tids.bit_count()
        if actual!=expected_count:
            raise AssertionError(f'Dataset recount mismatch: {sorted(items)} {actual}!={expected_count}')
    return {'itemsets_recounted':len(frequent),'count_mismatches':0,'passed':True}
