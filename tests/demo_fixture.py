"""Synthetic, local-only HTTP fixtures; no real customer or research data.

Creates tiny CSV/JSONL/PNG test artifacts outside the source tree. None of
these counts, metrics or images represent an experimental result.
"""
import csv
import json
from pathlib import Path
import struct
import uuid
import zlib


def make_demo_fixture(root):
    base=Path(root)/'results/recommendation_test_fixtures'/('demo_'+uuid.uuid4().hex)
    phase3, evaluation=base/'phase3',base/'evaluation'
    for path in (phase3/'data/public',phase3/'reports',phase3/'charts',
                 evaluation/'training_first',evaluation/'charts'):
        path.mkdir(parents=True,exist_ok=False)
    def write_json(path,value):
        with path.open('x',encoding='utf-8') as f:json.dump(value,f)
    def write_csv(path,rows):
        with path.open('x',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    products=[{'stock_code':code,'description':'Synthetic '+code,'reliable_name':'True'}
              for code in ('22386','21931','85099B','NO_RULE','EMPTY_NAME')]
    products[-1].update(description='',reliable_name='False')
    write_csv(phase3/'data/public/product_catalog.csv',products)
    rule=dict(rule_id='synthetic_rule',antecedents=['22386'],consequents=['85099B'],
              support_count=4,antecedent_support_count=5,consequent_support_count=6,
              n_transactions=10,support=.4,confidence=.8,lift=4/3)
    for path in (phase3/'data/public/recommendation_rules_enriched.jsonl',evaluation/'training_first/rules.jsonl'):
        with path.open('x',encoding='utf-8') as f:f.write(json.dumps(rule)+'\n')
    write_json(phase3/'reports/final_summary.json',dict(n_transactions=10,n_products=5,
               n_rules=1,n_products_with_reliable_names=4))
    write_json(evaluation/'metadata.json',dict(split={'train':8,'test':2,'boundary_day':'2010-01-02'},
               evaluation_baskets=1,test_singletons_excluded=1,training_catalog_size=3,
               cold_test_skus=0,cold_target_occurrences=0,reproducible=True,
               config={'seed':123,'fixture_only':True}))
    write_csv(phase3/'data/public/rfm_segments.csv',[{'segment':'Synthetic group','customers':2}])
    metrics=[dict(method=method,k=k,evaluation_baskets=1,precision=1/k,recall=1,
              hit_rate=1,ndcg=1,received=1,no_recommendation_rate=0,catalog_coverage=1/3,
              recommended_distinct_products=1,training_catalog_size=3)
             for method in ('popular','association_rules') for k in (5,10)]
    write_csv(evaluation/'metrics_summary.csv',metrics)
    # A real 1x1 RGB PNG generated with stdlib, used solely to test asset serving.
    def chunk(kind,value):
        return struct.pack('>I',len(value))+kind+value+struct.pack('>I',zlib.crc32(kind+value)&0xffffffff)
    png=(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))+
         chunk(b'IDAT',zlib.compress(b'\x00\x11\x77\x66'))+chunk(b'IEND',b''))
    for name in ('top_products','basket_sizes','monthly_transactions_coverage','rfm_segments'):
        (phase3/f'charts/{name}.png').write_bytes(png)
        write_csv(phase3/f'charts/{name}_data.csv',[{'fixture_only':True,'value':1}])
    for name in ('precision','recall','hit_rate','catalog_coverage','received'):
        (evaluation/f'charts/{name}.png').write_bytes(png)
        write_csv(evaluation/f'charts/{name}.csv',[{'fixture_only':True,'value':1}])
    return phase3,evaluation
