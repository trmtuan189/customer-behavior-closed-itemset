"""Exact rule ranking. Full-data rules are for descriptive demonstration only."""
from collections import defaultdict
from fractions import Fraction
import csv
import json
import math


def read_jsonl(path):
    with open(path, encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def load_catalog(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    return {r['stock_code']: {'stock_code': r['stock_code'],
            'description': r['description'] if r['reliable_name'].lower() == 'true' else r['stock_code'],
            'name_reliable': r['reliable_name'].lower() == 'true'} for r in rows}


def rule_score(r):
    """s*c*max(0,1-1/lift), computed from counts without rounding."""
    s = Fraction(r['support_count'], r['n_transactions'])
    c = Fraction(r['support_count'], r['antecedent_support_count'])
    lift = Fraction(r['support_count']*r['n_transactions'],
                    r['antecedent_support_count']*r['consequent_support_count'])
    return s*c*max(Fraction(0), 1-1/lift)


def valid_k(k):
    if type(k) is not int or not 1 <= k <= 100:
        raise ValueError('Top-K phải là số nguyên từ 1 đến 100.')


class RuleEngine:
    def __init__(self, catalog, rules):
        self.catalog = catalog
        self.rules = list(rules)
        self.index = defaultdict(list)
        self.scores = []
        self.sides = []
        for i, r in enumerate(self.rules):
            left, right = frozenset(r['antecedents']), frozenset(r['consequents'])
            if not left or not right or left & right:
                raise ValueError('Hai vế luật phải khác rỗng và rời nhau.')
            if not (left | right) <= catalog.keys():
                raise ValueError('Luật chứa mã ngoài catalog.')
            if not 0 < r['support_count'] <= min(r['antecedent_support_count'], r['consequent_support_count']) <= r['n_transactions']:
                raise ValueError('Support count không hợp lệ.')
            self.scores.append(rule_score(r))
            self.sides.append((left, right))
            for item in left:
                self.index[item].append(i)

    def search(self, query='', limit=50):
        query = query.strip().casefold()
        return [p for code, p in sorted(self.catalog.items())
                if query in code.casefold() or query in p['description'].casefold()][:limit]

    def recommend(self, basket, k=5, min_confidence=0.5, min_lift=1):
        valid_k(k)
        if isinstance(basket, str):
            basket = [basket]
        basket = frozenset(basket)
        unknown = sorted(basket-self.catalog.keys())
        if unknown:
            raise ValueError('StockCode không tồn tại: '+', '.join(unknown))
        if (not math.isfinite(min_confidence) or not 0 <= min_confidence <= 1 or
                not math.isfinite(min_lift) or min_lift < 0):
            raise ValueError('Ngưỡng confidence/lift không hợp lệ.')
        candidates = {}
        matching = sorted({i for x in basket for i in self.index.get(x, [])})
        for i in matching:
            r = self.rules[i]
            left, right = self.sides[i]
            conf = Fraction(r['support_count'], r['antecedent_support_count'])
            lift = Fraction(r['support_count']*r['n_transactions'],
                            r['antecedent_support_count']*r['consequent_support_count'])
            if not left <= basket or conf < Fraction(str(min_confidence)) or lift < Fraction(str(min_lift)):
                continue
            # Joint-consequent evidence is shared, NOT a marginal probability.
            # Max rather than sum prevents redundant, correlated rules inflating a SKU.
            key = (-self.scores[i], -conf, -lift,
                   -Fraction(r['support_count'], r['n_transactions']),
                   tuple(sorted(left)), tuple(sorted(right)), r['rule_id'])
            for item in right-basket:
                if item not in candidates or key < candidates[item][0]:
                    candidates[item] = (key, r, self.scores[i])
        ranked = sorted(candidates, key=lambda item: (candidates[item][0], item))[:k]
        return [{**self.catalog[item], 'score': float(candidates[item][2]),
                 'support': candidates[item][1]['support'],
                 'confidence': candidates[item][1]['confidence'],
                 'lift': candidates[item][1]['lift'], 'rule': candidates[item][1]}
                for item in ranked]


def popular_recommend(counts, basket, k):
    valid_k(k)
    return [x for x in sorted(counts, key=lambda x: (-counts[x], x)) if x not in basket][:k]
