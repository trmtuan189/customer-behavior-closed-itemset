"""Hand-computed fixtures: no production aggregation used to make the answers."""
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import ast
import hashlib
import os
import sys
import unittest
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from customer_analysis.core import (audit_source,product_catalog,link_transactions,rfm,
    enrich_rule,verify_public,midrank_scores,basket_analysis,customer_behavior,pseudonym,BAD_CODES,COLUMNS,customer_id,collapse_copy_name_votes)
from customer_analysis.excel_source import read_excel,xml_row_counts


def source_row(invoice='I1',code='001',name='  Blue   mug ',quantity=2,price=3,
               customer='PRIVATE-C1',day=1,sheet='Year 2009-2010',**changes):
    row={'_sheet':sheet,'Invoice':invoice,'StockCode':code,'Description':name,
         'Quantity':quantity,'Price':price,'Customer ID':customer,
         'InvoiceDate':datetime(2010,1,day),'Country':'UK'}
    row.update(changes)
    return row


class CustomerAnalysisTest(unittest.TestCase):
    def test_source_counts_cleaning_and_money_independent_constants(self):
        rows=[source_row(),source_row(code='B2',quantity=1,price=4),
              source_row(invoice='C2'),source_row(invoice='I3',quantity=-2),
              source_row(invoice='I4',price=0),source_row(invoice='I5',code='POST'),
              source_row(invoice='I6',price='oops'),source_row(invoice='I7',quantity=float('inf'))]
        groups,votes,quality=audit_source(rows)
        self.assertEqual(quality['source_rows'],8)
        self.assertEqual(quality['valid_rows'],2)
        self.assertEqual(len(groups),1)
        # Literal arithmetic: 2*3 + 1*4 = 10; quantity = 3.
        g=groups['Year 2009-2010','I1']
        self.assertEqual(g['amount_source'],Decimal('10'))
        self.assertEqual(g['quantity_source'],Decimal('3'))
        self.assertEqual(g['items'],{'001','B2'})

    def test_missing_customer_not_one_shared_customer(self):
        groups,_,_=audit_source([source_row(customer=None),source_row(invoice='I2',customer=None)])
        linked,summary=link_transactions({'I1':['001'],'I2':['001']},groups)
        self.assertEqual(summary['verified_linked'],2)
        self.assertTrue(all(r['customer_id'] is None for r in linked))
        customers,months,freq=customer_behavior(linked)
        self.assertEqual(customers,[])
        self.assertEqual(freq,{})
        self.assertEqual(months[0]['known_active_customers'],0)

    def test_customer_placeholder_strings_are_missing_not_shared_ids(self):
        for value in [None,float('nan'),'','  ','nan','N/A','NULL','<NA>','unknown']:
            self.assertIsNone(customer_id(value))
        self.assertEqual(customer_id(12345),'12345')
        self.assertEqual(customer_id(12345.0),'12345')
        groups,_,_=audit_source([source_row(customer='N/A'),source_row(invoice='I2',customer='nan')])
        linked,_=link_transactions({'I1':['001'],'I2':['001']},groups)
        self.assertEqual(customer_behavior(linked)[0],[])

    def test_catalog_spaces_case_alias_and_vote_ties(self):
        groups,votes,_=audit_source([source_row(),source_row(invoice='I2',name='blue MUG'),
            source_row(invoice='I3',name=' RED mug '),source_row(invoice='I4',code='X',name='Alpha'),
            source_row(invoice='I5',code='X',name='Beta')])
        catalog={r['stock_code']:r for r in product_catalog({'001':3,'X':2,'Z':1},votes,source_available=True)}
        self.assertEqual(catalog['001']['description'],'BLUE MUG')
        self.assertEqual(catalog['001']['n_descriptions'],2)
        self.assertTrue(catalog['001']['reliable_name'])
        self.assertIsNone(catalog['X']['description'])
        self.assertEqual(catalog['X']['representative_candidate'],'ALPHA')
        self.assertEqual(catalog['X']['name_status'],'ambiguous_tie')
        self.assertIsNone(catalog['Z']['description'])
        # Repeated lines in one invoice are not independent name votes.
        _,single_votes,_=audit_source([source_row(),source_row()])
        self.assertFalse(product_catalog({'001':1},single_votes,source_available=True)[0]['reliable_name'])

    def test_stockcodes_not_coerced_to_numbers_or_uppercase(self):
        _,votes,_=audit_source([source_row(),source_row(invoice='I2',code='a1'),source_row(invoice='I3',code='A1')])
        self.assertEqual(set(votes),{'001','a1','A1'})

    def test_cross_sheet_collision_is_not_merged_even_when_baskets_equal(self):
        groups,_,_=audit_source([source_row(),source_row(sheet='Year 2010-2011')])
        linked,summary=link_transactions({'I1':['001']},groups)
        self.assertEqual(summary['verified_linked'],0)
        self.assertEqual(linked[0]['link_status'],'ambiguous_cross_worksheet')

    def test_proven_sheet_copy_is_one_invoice_not_double_money_or_votes(self):
        groups,votes,_=audit_source([source_row(),source_row(sheet='Year 2010-2011')])
        proof={'I1':{'identical_multisets':True,'source_keys':list(groups)}}
        linked,summary=link_transactions({'I1':['001']},groups,proof)
        self.assertEqual(summary['verified_linked'],1)
        self.assertEqual(summary['resolved_identical_cross_sheet_invoices'],1)
        self.assertEqual(linked[0]['amount_source'],'6')
        self.assertEqual(linked[0]['quantity_source'],'2')
        self.assertEqual(collapse_copy_name_votes(votes,linked),1)
        self.assertFalse(product_catalog({'001':1},votes,source_available=True)[0]['reliable_name'])

    def test_copy_resolution_refuses_different_metadata_or_wrong_provenance(self):
        groups,_,_=audit_source([source_row(),source_row(sheet='Year 2010-2011',price=4)])
        proof={'I1':{'identical_multisets':True,'source_keys':list(groups)}}
        linked,summary=link_transactions({'I1':['001']},groups,proof)
        self.assertEqual(summary['verified_linked'],0)
        proof={'I1':{'identical_multisets':True,'source_keys':[]}}
        self.assertEqual(link_transactions({'I1':['001']},groups,proof)[1]['verified_linked'],0)

    def test_independent_raw_copy_proof_accepts_reordered_rows_only(self):
        import openpyxl
        sys.path.insert(0,str(ROOT/'src/validation'))
        from run_customer_analysis import independent_source_check
        base=Path(os.environ.get('CUSTOMER_TEST_ROOT',str(ROOT/'results/customer_analysis/test_fixtures')))
        path=base/uuid.uuid4().hex/'reordered_synthetic_source.xlsx'
        path.parent.mkdir(parents=True)
        wb=openpyxl.Workbook();wb.active.title='Year 2009-2010';wb.create_sheet('Year 2010-2011')
        a,b=source_row(),source_row(code='B2')
        for sheet,rows in zip(wb,[[a,b],[b,a]]):
            sheet.append(list(COLUMNS))
            for row in rows: sheet.append([row[c] for c in COLUMNS])
        with path.open('xb') as f: wb.save(f)
        wb.close()
        groups,_,quality=audit_source(read_excel(path))
        check,proofs=independent_source_check(path,quality,groups)
        self.assertTrue(proofs['I1']['identical_multisets'])
        self.assertEqual(check['proven_identical_cross_sheet_multisets'],1)
        self.assertEqual(len(set(proofs['I1']['multiset_sha256'])),1)
        self.assertEqual(link_transactions({'I1':['001','B2']},groups,proofs)[1]['verified_linked'],1)

    def test_duplicate_lines_audited_and_excluded_from_monetary(self):
        groups,_,quality=audit_source([source_row(),source_row()])
        linked,_=link_transactions({'I1':['001']},groups)
        self.assertEqual(quality['valid_rows'],2)
        self.assertEqual(quality['worksheets']['Year 2009-2010']['exact_duplicate_rows'],1)
        self.assertEqual(linked[0]['amount_source'],'12')
        self.assertEqual(linked[0]['amount_deduplicated_sensitivity'],'6')
        self.assertFalse(linked[0]['monetary_eligible'])
        self.assertIsNone(linked[0]['order_value'])
        self.assertEqual(rfm(linked)[0],[])

    def test_missing_and_conflicting_metadata_do_not_invent_values(self):
        groups,_,_=audit_source([source_row(),source_row(code='B2',customer=None,InvoiceDate=None)])
        linked,_=link_transactions({'I1':['001','B2'],'not-found':['001'],'wrong':['X']},groups)
        self.assertIsNone(linked[0]['customer_id'])
        self.assertIsNone(linked[0]['invoice_date'])
        self.assertFalse(linked[0]['monetary_eligible'])
        self.assertEqual(linked[1]['link_status'],'source_invoice_not_found')
        mismatch,_=link_transactions({'I1':['001']},groups)
        self.assertEqual(mismatch[0]['link_status'],'basket_mismatch')

    def test_monthly_and_customer_aggregates_literal_answers(self):
        groups,_,_=audit_source([source_row(),source_row(invoice='I2',quantity=1,price=5,day=10),
                                source_row(invoice='I3',customer='PRIVATE-C2',quantity=1,price=4,day=5)])
        linked,_=link_transactions({'I1':['001'],'I2':['001'],'I3':['001']},groups)
        customers,months,freq=customer_behavior(linked)
        self.assertEqual(months,[{'month':'2010-01','invoices':3,'known_active_customers':2,
                                  'gross_value_eligible':'15','invoices_with_value':3}])
        self.assertEqual(dict(freq),{2:1,1:1})
        self.assertEqual(sorted(c['distinct_products'] for c in customers),[1,1])
        self.assertEqual(sorted(Decimal(c['known_order_value']) for c in customers),[Decimal(4),Decimal(11)])

    def test_rfm_reference_is_next_data_day_and_ties_not_split(self):
        groups,_,_=audit_source([source_row(),source_row(invoice='I2',quantity=1,price=5,day=10),
                                source_row(invoice='I3',customer='PRIVATE-C2',quantity=1,price=4,day=5)])
        linked,_=link_transactions({'I1':['001'],'I2':['001'],'I3':['001']},groups)
        rows,summary=rfm(linked)
        self.assertEqual(summary['reference_date'],'2010-01-11')
        self.assertEqual([(r['recency_days'],r['frequency'],r['monetary']) for r in rows],[(1,2,'11'),(6,1,'4')])
        self.assertEqual(midrank_scores([1,1,1,1]),[3,3,3,3])
        self.assertEqual(midrank_scores([10,10,20,20]),[2,2,4,4])
        self.assertEqual(midrank_scores([10,10,20,20],True),[4,4,2,2])
        with self.assertRaises(ValueError): rfm(linked+[linked[0]])

    def test_basket_statistics_independent_expected_counts(self):
        overview,products,sizes=basket_analysis([['001','B'],['001'],['B','C'],['001','B','C']])
        self.assertEqual(products,Counter({'001':3,'B':3,'C':2}))
        self.assertEqual(sizes,Counter({2:2,1:1,3:1}))
        self.assertEqual(overview['basket_size']['mean'],2)
        self.assertEqual(overview['basket_size']['median'],2)

    def test_rule_metrics_and_public_privacy_canary(self):
        rule={'rule_id':'r1','antecedents':['001'],'consequents':['B'],
              'support':.5,'confidence':.75,'lift':1.5,'support_count':3}
        catalog={'001':{'description':'BLUE MUG','name_status':'verified_source_name'}}
        enriched=enrich_rule(rule,catalog,'a'*64)
        self.assertTrue(all(enriched[k]==v for k,v in rule.items()))
        self.assertIsNone(enriched['consequents_products'][0]['description'])
        self.assertTrue(verify_public(enriched))
        with self.assertRaises(ValueError): verify_public({'nested':[{'Customer ID':'PRIVATE-C1'}]})
        token=pseudonym('PRIVATE-C1',b'secret')
        self.assertNotIn('PRIVATE-C1',token)
        self.assertEqual(token,pseudonym('PRIVATE-C1',b'secret'))
        self.assertNotEqual(token,pseudonym('PRIVATE-C1',b'other'))

    def test_missing_excel_does_not_create_fictional_catalog_names(self):
        catalog=product_catalog({'001':3,'B':4})
        self.assertTrue(all(r['description'] is None and not r['reliable_name']
                            and r['name_status']=='missing_source' for r in catalog))

    def test_resource_guard_does_not_return_partial_success(self):
        with self.assertRaises(ValueError): audit_source([source_row(),source_row()],max_rows=1)

    def test_special_code_filter_matches_original_pipeline_ast(self):
        source=ast.parse((ROOT/'src/preprocessing/build_transactions_v1.py').read_text(encoding='utf-8'))
        original=[ast.literal_eval(n.value) for n in source.body if isinstance(n,ast.Assign)
                  and any(isinstance(t,ast.Name) and t.id=='bad' for t in n.targets)]
        self.assertEqual(len(original),1)
        self.assertEqual(set(BAD_CODES),original[0])

    def test_two_sheet_excel_and_independent_xml_row_counts(self):
        import openpyxl
        base=Path(os.environ.get('CUSTOMER_TEST_ROOT',str(ROOT/'results/customer_analysis/test_fixtures')))
        path=base/uuid.uuid4().hex/'synthetic_source.xlsx'
        path.parent.mkdir(parents=True)
        workbook=openpyxl.Workbook()
        first=workbook.active
        first.title='Year 2009-2010'
        second=workbook.create_sheet('Year 2010-2011')
        for sheet in [first,second]: sheet.append(list(COLUMNS))
        raw=[source_row(),source_row(invoice='C2'),source_row(invoice='I3',quantity=-1),
             source_row(invoice='I2',code='B2',customer=None,sheet='Year 2010-2011'),
             source_row(sheet='Year 2010-2011')]
        for row in raw:
            workbook[row['_sheet']].append([row[c] for c in COLUMNS])
        with path.open('xb') as f: workbook.save(f)
        workbook.close()
        before=hashlib.sha256(path.read_bytes()).hexdigest()
        physical=xml_row_counts(path)
        self.assertEqual(physical,{'Year 2009-2010':{'physical_data_rows':3,'nonempty_data_rows':3},
                                  'Year 2010-2011':{'physical_data_rows':2,'nonempty_data_rows':2}})
        groups,votes,q=audit_source(read_excel(path))
        self.assertEqual(q['source_rows'],5)
        self.assertEqual(q['valid_rows'],3)
        self.assertEqual([q['worksheets'][s]['rows'] for s in ['Year 2009-2010','Year 2010-2011']],[3,2])
        _,links=link_transactions({'I1':['001'],'I2':['B2']},groups)
        self.assertEqual(links['verified_linked'],1)
        self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())


if __name__=='__main__':
    unittest.main()
