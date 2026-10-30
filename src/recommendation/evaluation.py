"""Temporal split, fixed masking and macro metrics; no tuning on test."""
from datetime import datetime
import hashlib
import math
import random


def temporal_split(rows, train_ratio=.8):
    if not 0 < train_ratio < 1:
        raise ValueError('train_ratio must be in (0,1)')
    usable, excluded = [], 0
    for r in rows:
        if r.get('link_status') != 'verified' or not r.get('invoice_date'):
            excluded += 1
            continue
        try:
            day = datetime.fromisoformat(r['invoice_date']).date().isoformat()
        except ValueError:
            excluded += 1
            continue
        usable.append({**r, 'split_day': day})
    usable.sort(key=lambda r: (r['invoice_date'], r['invoice']))
    if len(usable) < 2:
        raise ValueError('Need at least two dated transactions')
    boundary = usable[min(len(usable)-1, int(len(usable)*train_ratio))]['split_day']
    train = [r for r in usable if r['split_day'] < boundary]
    test = [r for r in usable if r['split_day'] >= boundary]
    if not train or not test:
        raise ValueError('Date boundary produced an empty split')
    return train, test, {'boundary_day': boundary, 'train': len(train), 'test': len(test),
                         'excluded_missing_or_unverified_date': excluded,
                         'boundary_policy': 'all invoices on boundary day go to test',
                         'train_max_date': max(r['invoice_date'] for r in train),
                         'test_min_date': min(r['invoice_date'] for r in test)}


def mask_basket(items, seed, case_id):
    items = sorted(set(items))
    if len(items) < 2:
        return None
    digest = hashlib.sha256(f'{seed}:{case_id}'.encode()).digest()
    random.Random(int.from_bytes(digest, 'big')).shuffle(items)
    n_known = max(1, len(items)//2)
    return sorted(items[:n_known]), sorted(items[n_known:])


def metrics(predicted, target, k):
    if k <= 0 or not target or len(predicted) != len(set(predicted)):
        raise ValueError('Invalid metric input')
    predicted = predicted[:k]
    hits = [int(x in target) for x in predicted]
    n = sum(hits)
    dcg = sum(h/math.log2(i+2) for i, h in enumerate(hits))
    ideal = sum(1/math.log2(i+2) for i in range(min(k, len(target))))
    return {'precision': n/k, 'recall': n/len(target), 'hit_rate': int(n > 0),
            'ndcg': dcg/ideal, 'received': int(bool(predicted))}
