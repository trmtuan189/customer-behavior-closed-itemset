import copy
import csv
from fractions import Fraction
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from recommendation.engine import RuleEngine, rule_score, load_catalog, popular_recommend
from recommendation.evaluation import temporal_split, mask_basket, metrics


def rule(left=('A',), right=('B',), ident='r1', count=4, ca=5, cb=6, n=10):
    return dict(rule_id=ident, antecedents=list(left), consequents=list(right),
        support_count=count, antecedent_support_count=ca, consequent_support_count=cb,
        n_transactions=n, support=count/n, confidence=count/ca, lift=count*n/(ca*cb))


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.catalog = {x: {'stock_code': x, 'description': 'Product '+x, 'name_reliable': True} for x in 'ABCDE'}
        self.engine = RuleEngine(self.catalog, [rule()])

    def test_single(self):
        self.assertEqual([x['stock_code'] for x in self.engine.recommend('A')], ['B'])

    def test_full_antecedent(self):
        e = RuleEngine(self.catalog, [rule(('A','C'))])
        self.assertEqual(e.recommend(['A']), [])
        self.assertEqual(e.recommend(['A','C'])[0]['stock_code'], 'B')

    def test_multi_consequent(self):
        e = RuleEngine(self.catalog, [rule(right=('B','C'))])
        rows = e.recommend(['A'])
        self.assertEqual([r['stock_code'] for r in rows], ['B','C'])
        self.assertEqual(rows[0]['score'], rows[1]['score'])
        self.assertEqual(rows[0]['confidence'], .8)

    def test_exclude_input(self):
        self.assertEqual(self.engine.recommend(['A','B']), [])

    def test_dedupe_max_not_sum(self):
        r = rule(ident='r2', count=3)
        e = RuleEngine(self.catalog, [r, rule(), rule(ident='r3')])
        rows = e.recommend(['A'])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['rule']['rule_id'], 'r1')
        self.assertEqual(rows[0]['score'], float(rule_score(rule())))

    def test_no_matching(self):
        self.assertEqual(self.engine.recommend(['C']), [])
        self.assertEqual(self.engine.recommend([]), [])

    def test_unknown(self):
        with self.assertRaisesRegex(ValueError, 'không tồn tại'):
            self.engine.recommend(['Z'])

    def test_invalid_k(self):
        for k in (0, -1, 101, True, 2.5):
            with self.assertRaises(ValueError):
                self.engine.recommend(['A'], k)

    def test_top_k(self):
        e = RuleEngine(self.catalog, [rule(right=('B','C','D'))])
        self.assertEqual(len(e.recommend(['A'], 1)), 1)

    def test_formula_exact(self):
        self.assertEqual(rule_score(rule()), Fraction(4,10)*Fraction(4,5)*(1-Fraction(30,40)))

    def test_threshold(self):
        self.assertEqual(self.engine.recommend(['A'], min_confidence=.81), [])
        self.assertEqual(self.engine.recommend(['A'], min_lift=2), [])
        for conf, lift in ((float('nan'),1),(.5,float('inf')),(-1,1),(1.1,1),(.5,-1)):
            with self.assertRaises(ValueError):
                self.engine.recommend(['A'], min_confidence=conf, min_lift=lift)

    def test_no_mutation(self):
        rules = [rule()]
        before = copy.deepcopy(rules)
        RuleEngine(self.catalog,rules).recommend(['A'])
        self.assertEqual(before, rules)

    def test_search(self):
        self.assertEqual(self.engine.search('product a')[0]['stock_code'], 'A')
        self.assertEqual(self.engine.search('not found'), [])

    def test_name_fallback(self):
        d = ROOT/'results/recommendation_test_fixtures'/uuid.uuid4().hex
        d.mkdir(parents=True)
        p = d/'catalog.csv'
        p.write_text('stock_code,description,reliable_name\n001,,False\n002,Trusted,True\n', encoding='utf-8')
        c = load_catalog(p)
        self.assertEqual(c['001']['description'], '001')
        self.assertEqual(c['002']['description'], 'Trusted')

    def test_deterministic_order(self):
        rules = [rule(right=(x,), ident=x) for x in 'BCD']
        a = RuleEngine(self.catalog, rules).recommend(['A'])
        b = RuleEngine(self.catalog, list(reversed(rules))).recommend(['A'])
        self.assertEqual(a, b)

    def test_popular_training_only(self):
        self.assertEqual(popular_recommend({'A':10,'B':4,'C':4}, {'A'}, 5), ['B','C'])

    def test_date_tie_whole_day(self):
        rows = [dict(invoice=str(i),items=['A'],link_status='verified',invoice_date=d) for i,d in enumerate([
            '2010-01-01T10:00:00','2010-01-02T10:00:00','2010-01-03T09:00:00',
            '2010-01-03T10:00:00','2010-01-03T11:00:00'])]
        a,b,s = temporal_split(rows)
        self.assertEqual(len(a),2)
        self.assertEqual(len(b),3)
        self.assertEqual(s['boundary_day'],'2010-01-03')

    def test_missing_dates_excluded(self):
        rows = [dict(invoice='1',invoice_date='2010-01-01',link_status='verified'),
                dict(invoice='2',invoice_date='2010-01-02',link_status='verified'),
                dict(invoice='3',invoice_date=None,link_status='verified')]
        self.assertEqual(temporal_split(rows)[2]['excluded_missing_or_unverified_date'],1)

    def test_mask_reproducible(self):
        a = mask_basket(['C','A','B','D','E'],42,1)
        self.assertEqual(a,mask_basket(['E','D','C','B','A'],42,1))
        self.assertFalse(set(a[0]) & set(a[1]))
        self.assertEqual(set(a[0]+a[1]),set('ABCDE'))
        self.assertEqual(len(a[0]),2)
        self.assertIsNone(mask_basket(['A'],42,1))

    def test_metrics_short_list(self):
        r = metrics(['B'],{'B','C'},5)
        self.assertEqual(r['precision'],.2)
        self.assertEqual(r['recall'],.5)
        self.assertEqual(r['hit_rate'],1)

    def test_empty_metrics_not_removed(self):
        r = metrics([],{'B'},5)
        self.assertEqual(r,dict(precision=0,recall=0,hit_rate=0,ndcg=0,received=0))

    def test_ndcg(self):
        self.assertEqual(metrics(['B','C'],{'B','C'},5)['ndcg'],1)

    def test_invalid_rules(self):
        for r in (rule(left=()),rule(right=('A',)),rule(right=('Z',)),rule(count=0)):
            with self.assertRaises(ValueError):
                RuleEngine(self.catalog,[r])


if __name__ == '__main__':
    unittest.main()
