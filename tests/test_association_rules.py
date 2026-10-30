"""Hand-calculated, independent exhaustive and randomized correctness tests."""
from pathlib import Path
import random
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'src/validation'))
from rules.association_rules import (Thresholds, ResourceLimitError, recover_frequent,
                                     generate_rules, top_rules, ranking_key)
from association_rule_reference import brute_force_itemsets, reference_rules, compare_rules


class AssociationRulesTest(unittest.TestCase):
    def check_dataset(self, transactions, support, confidence, lift):
        frequent,closed = brute_force_itemsets(transactions,support)
        t = Thresholds(support,confidence,lift)
        reconstructed = recover_frequent(closed,len(transactions),t)
        self.assertEqual(reconstructed,frequent)
        rules,summary = generate_rules(reconstructed,len(transactions),t,'tiny-dataset')
        reference,counts = reference_rules(frequent,len(transactions),confidence,lift)
        compare_rules(rules,reference)
        for key,value in counts.items():
            self.assertEqual(summary[key],value)
        self.assertEqual(summary['n_pass_lift_after_confidence'],len(rules))
        self.assertEqual(summary['n_candidates'],summary['n_rejected_confidence']+
                         summary['n_rejected_lift_after_confidence']+summary['n_final'])
        return frequent,closed,rules,summary

    def test_hand_calculated_support_metrics_and_filters(self):
        tx=[['A','B'],['A','B'],['A','B','C'],['A','C'],['C'],['A','B','C']]
        freq,closed,rules,summary = self.check_dataset(tx,'0.3','0.5','1')
        self.assertEqual(freq,{frozenset('A'):5,frozenset('B'):4,frozenset('C'):4,
            frozenset('AB'):4,frozenset('AC'):3,frozenset('BC'):2,frozenset('ABC'):2})
        self.assertEqual(closed,{frozenset('A'):5,frozenset('C'):4,
            frozenset('AB'):4,frozenset('AC'):3,frozenset('ABC'):2})
        self.assertEqual(summary['n_candidates'],12)
        self.assertEqual(summary['n_pass_confidence'],11)
        self.assertEqual(summary['n_pass_lift_independent'],6)
        self.assertEqual(summary['n_final'],5)
        keyed={(tuple(r['antecedents']),tuple(r['consequents'])):r for r in rules}
        r=keyed[('A',),('B',)]
        self.assertEqual((r['support_count'],r['antecedent_support_count'],r['consequent_support_count']), (4,5,4))
        self.assertAlmostEqual(r['support'],2/3)
        self.assertAlmostEqual(r['confidence'],.8)
        self.assertAlmostEqual(r['lift'],1.2)
        self.assertEqual(keyed[('B',),('A',)]['confidence'],1)
        _,_,strict,c = self.check_dataset(tx,'0.3','0.9','1')
        self.assertEqual(c['n_pass_confidence'],2)
        self.assertEqual(len(strict),2)

    def test_zero_rules_empty_and_singletons(self):
        for tx in [[['A'],['B']], [['A'],['A']]]:
            _,_,rules,s=self.check_dataset(tx,'0.5','0.5','1')
            self.assertEqual(rules,[])
            self.assertEqual(s['n_candidates'],0)
        rules,s=generate_rules({},4,Thresholds('0.5'),'tiny')
        self.assertEqual(rules,[])
        self.assertIsNone(s['final_lift']['mean'])

    def test_always_together_and_confidence_one(self):
        _,closed,rules,_=self.check_dataset([['A','B'],['A','B'],[],[]],'0.5','1','1')
        self.assertEqual(closed,{frozenset('AB'):2})
        self.assertEqual(len(rules),2)
        self.assertTrue(all(r['confidence']==1 and r['lift']==2 for r in rules))

    def test_lift_exactly_one_and_threshold_boundary(self):
        tx=[['A','B'],['A'],['B'],[]]
        _,_,rules,_=self.check_dataset(tx,'0.25','0.5','1')
        self.assertEqual(len(rules),2)
        self.assertTrue(all(r['lift']==1 for r in rules))
        self.assertEqual(self.check_dataset(tx,'0.25','0.5000000000000001','1')[2],[])
        self.assertEqual(self.check_dataset(tx,'0.25','0.5','1.0000000000000001')[2],[])

    def test_both_sides_multiple_items(self):
        _,_,rules,s=self.check_dataset([list('ABCD')]*3+[[]],'0.5','0','0')
        self.assertEqual(s['n_candidates'],50)  # 12 pairs + 24 triples + 14 quadruple partitions.
        self.assertTrue(any(len(r['antecedents'])==2 and len(r['consequents'])==2 for r in rules))

    def test_ranking_ties_top_k_ids_and_stockcode_preservation(self):
        _,_,rules,s=self.check_dataset([['001','A','b']]*3+[[]],'0.5','0','0')
        for criterion in ['confidence','lift','support','balanced']:
            a=top_rules(rules,criterion,3)
            b=top_rules(list(reversed(rules)),criterion,3)
            self.assertEqual(a,b)
            # In this always-together dataset all numeric scores tie. The
            # expected order therefore comes directly from lexical rule sides.
            expected=sorted(rules,key=lambda r:(tuple(r['antecedents']),tuple(r['consequents'])))[:3]
            self.assertEqual(a,expected)
        self.assertEqual(s['n_final'],12)
        self.assertEqual(len({r['rule_id'] for r in rules}),12)
        self.assertTrue(any('001' in r['antecedents'] for r in rules))
        t=Thresholds('0.25','0','0')
        freq,_=brute_force_itemsets([['001','A','b']]*3+[[]],'.25')
        other,_=generate_rules(freq,4,t,'tiny-dataset')
        self.assertEqual({r['rule_id'] for r in rules},{r['rule_id'] for r in other})
        with self.assertRaises(ValueError): top_rules(rules,k=0)

    def test_invalid_thresholds_counts_and_missing_subsets(self):
        for support in ['0','-0.1','1.01','nan','inf','oops']:
            with self.subTest(support=support),self.assertRaises(ValueError): Thresholds(support)
        for confidence in ['-0.1','1.1','nan','inf']:
            with self.subTest(confidence=confidence),self.assertRaises(ValueError): Thresholds('.5',confidence)
        for lift in ['-1','nan','inf']:
            with self.assertRaises(ValueError): Thresholds('.5','.5',lift)
        t=Thresholds('.5')
        for count in [0,5,True,2.0]:
            with self.assertRaises(ValueError): recover_frequent({frozenset('A'):count},4,t)
        with self.assertRaises(ValueError): generate_rules({frozenset('AB'):2},4,t,'x')
        with self.assertRaises(ValueError): recover_frequent({frozenset('A'):2,frozenset('AB'):2},4,t)
        with self.assertRaises(ValueError): recover_frequent({},0,t)

    def test_ranking_primary_metrics_have_distinct_winners(self):
        rows=[]
        for item,cx,cy,count in [('A',20,50,10),('B',10,10,9),('C',40,40,30)]:
            rows.append({'antecedents':[item],'consequents':['Y'+item],
                         'support_count':count,'antecedent_support_count':cx,
                         'consequent_support_count':cy,'n_transactions':100})
        for criterion,winner in [('support','C'),('confidence','B'),('lift','B'),('balanced','C')]:
            self.assertEqual(top_rules(rows,criterion,1)[0]['antecedents'],[winner])

    def test_caps_abort_instead_of_truncating(self):
        closed={frozenset('ABCD'):3}
        t=Thresholds('.5')
        with self.assertRaises(ResourceLimitError): recover_frequent(closed,4,t,max_subset_visits=14)
        with self.assertRaises(ResourceLimitError): recover_frequent(closed,4,t,max_frequent=14)
        frequent=recover_frequent(closed,4,t)
        with self.assertRaises(ResourceLimitError): generate_rules(frequent,4,t,'x',max_candidates=49)

    def test_randomized_exhaustive_oracle_360_configurations(self):
        rng=random.Random(271026)
        for case in range(120):
            products=list('ABCDEF')[:rng.randint(1,6)]
            tx=[[x for x in products if rng.random()<.55] for _ in range(rng.randint(1,12))]
            for support in ['0.2','0.5','0.75']:
                confidence=rng.choice(['0','0.5','1'])
                lift=rng.choice(['0','1','1.5'])
                with self.subTest(case=case,support=support):
                    self.check_dataset(tx,support,confidence,lift)


if __name__=='__main__':
    unittest.main()
