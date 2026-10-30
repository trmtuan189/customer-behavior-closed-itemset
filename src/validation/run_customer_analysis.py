"""Phase 3: read-only enrichment and auditable customer analysis.

python -B src/validation/run_customer_analysis.py --smoke-test
python -B src/validation/run_customer_analysis.py
python -B src/validation/run_customer_analysis.py --xlsx data/raw/online_retail_II.xlsx

Missing Excel yields PARTIAL status and measured basket/rule analysis, never
fabricated names, customers, dates, revenue or RFM. Every run is exclusive.
"""
from __future__ import annotations

import argparse
from collections import Counter,defaultdict
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import secrets
import subprocess
import sys
import traceback
import uuid

import psutil

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from customer_analysis.core import (COLUMNS,audit_source,basket_analysis,product_catalog,
    link_transactions,customer_behavior,rfm,pseudonym,enrich_rule,verify_public,descriptive,collapse_copy_name_votes)
from customer_analysis.excel_source import read_excel,xml_row_counts
from rules.association_rules import top_rules
from benchmark_runner import write_json,file_hash,git_info,utc_now
from common import load_transactions


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def read_rows(path):
    with path.open(encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def jsonl_new(path,rows):
    with path.open('x',encoding='utf-8') as f:
        for r in rows: f.write(json.dumps(r,ensure_ascii=False,allow_nan=False)+'\n')


def csv_new(path,rows,fields):
    import csv
    with path.open('x',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k:json.dumps(row.get(k),ensure_ascii=False) if isinstance(row.get(k),(list,dict))
                             else row.get(k) for k in fields})


def preserve_snapshot(run):
    files=[ROOT/'README.md',ROOT/'.gitignore']
    for directory in ['src','tests','data','results']:
        files.extend(p for p in (ROOT/directory).rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts and not p.is_relative_to(run))
    return {str(p.relative_to(ROOT)):file_hash(p) for p in files}


def free_guard(minimum):
    if psutil.virtual_memory().available<minimum*1048576:
        raise MemoryError(f'Available RAM below {minimum} MiB; stop, no success claim')


def find_excel(explicit):
    if explicit:
        return explicit.resolve() if explicit.is_file() else None,{
            'requested':str(explicit),'found':explicit.is_file(),'search_scope':'explicit user path'}
    candidates=sorted(set(list((ROOT/'data').rglob('online_retail_II.xlsx'))+
                          list(ROOT.glob('online_retail_II.xlsx'))))
    # Never treat our synthetic fixtures or previous exports as real raw data.
    hashes={str(p.resolve()):file_hash(p) for p in candidates}
    selected=candidates[0] if candidates and len(set(hashes.values()))==1 else None
    return selected,{'searched':['project root/online_retail_II.xlsx','data/**/online_retail_II.xlsx'],
        'candidates_sha256':hashes,'selected':str(selected) if selected else None,
        'reason':'missing' if not candidates else 'multiple different workbooks' if selected is None else 'unique byte-identical source',
        'synthetic_and_results_directories_excluded':True}


def choose_rules(explicit,sha):
    if explicit:
        return explicit.resolve()
    eligible=[]
    for path in (ROOT/'results/association_rules').glob('*/metadata.json'):
        meta=read_json(path)
        done=path.parent/'completion.json'
        if meta.get('mode')=='real' and meta.get('source_dataset_hash')==sha and done.exists():
            if read_json(done).get('status')=='completed' and read_json(path.parent/'validation_report.json').get('all_passed'):
                eligible.append(path.parent/'recommendation_rules.jsonl')
    if not eligible: raise ValueError('No validated real association-rule catalog matches dataset SHA256')
    return sorted(eligible)[-1]


def independent_source_check(path,quality,groups):
    """Second pass from raw cells, independent cleaning predicates and sums."""
    counts=Counter()
    valid=0
    oracle=defaultdict(lambda:{'items':set(),'quantity':Decimal(0),'amount':Decimal(0)})
    repeated={invoice for invoice,count in Counter(k[1] for k in groups).items() if count>1}
    overlap_rows=defaultdict(Counter)
    excluded={'B','ADJUST','ADJUST2','AMAZONFEE','BANK CHARGES','C2','DOT','POST','D','23444','M','m'}
    for row in read_excel(path):
        counts[row['_sheet']]+=1
        inv=row.get('Invoice'); code=row.get('StockCode')
        if inv is None or code is None: continue
        inv,code=str(inv).strip(),str(code).strip()
        if not inv or not code or inv.upper().startswith('C') or code in excluded: continue
        try:
            q,p=Decimal(str(row['Quantity'])),Decimal(str(row['Price']))
            if not q.is_finite() or not p.is_finite() or q<=0 or p<=0: continue
        except Exception:
            continue
        valid+=1
        g=oracle[row['_sheet'],inv]
        g['items'].add(code)
        g['quantity']+=q
        g['amount']+=q*p
        if inv in repeated:
            raw_fingerprint=hashlib.sha256(repr(tuple(repr(row.get(c)) for c in COLUMNS)).encode('utf-8')).digest()
            overlap_rows[row['_sheet'],inv][raw_fingerprint]+=1
    if valid!=quality['valid_rows'] or sum(counts.values())!=quality['source_rows']:
        raise AssertionError('Independent source counts differ')
    if set(oracle)!=set(groups): raise AssertionError('Independent invoice keys differ')
    for key,r in oracle.items():
        actual=groups[key]
        if (r['items']!=actual['items'] or r['quantity']!=actual['quantity_source']
                or r['amount']!=actual['amount_source']):
            raise AssertionError('Independent raw invoice aggregation differs')
    physical=xml_row_counts(path)
    for sheet,stats in quality['worksheets'].items():
        if stats['rows']-stats['blank_rows']!=physical[sheet]['nonempty_data_rows']:
            raise AssertionError('XML/source nonempty row counts differ')
    proofs={}
    for invoice in sorted(repeated):
        keys=sorted(k for k in overlap_rows if k[1]==invoice)
        identical=len(keys)>1 and all(overlap_rows[k]==overlap_rows[keys[0]] for k in keys[1:])
        fingerprints=[]
        for key in keys:
            digest=hashlib.sha256()
            for row_hash,count in sorted(overlap_rows[key].items()):
                digest.update(row_hash)
                digest.update(count.to_bytes(8,'big'))
            fingerprints.append(digest.hexdigest())
        proofs[invoice]={'identical_multisets':identical,'source_keys':keys,
                         'row_counts':[sum(overlap_rows[k].values()) for k in keys],
                         'multiset_sha256':fingerprints}
    return {'status':'passed','source_rows':sum(counts.values()),'valid_rows':valid,
            'xml_rows':physical,'invoice_groups_checked':len(oracle),
            'cross_sheet_invoices_checked':len(proofs),
            'proven_identical_cross_sheet_multisets':sum(r['identical_multisets'] for r in proofs.values()),
            'scope':'Independent raw row cleaning, basket sets, source quantity/value totals, XML row counts and full eight-column multiset equality; RFM correctness additionally tested on hand fixtures'},proofs


def smoke_fixture(run):
    import openpyxl
    from customer_analysis.core import COLUMNS
    from rules.association_rules import rule_id
    directory=run/'tests/internal'
    xlsx=directory/'synthetic_source.xlsx'
    wb=openpyxl.Workbook()
    wb.active.title='Year 2009-2010'
    wb.create_sheet('Year 2010-2011')
    for sheet in wb: sheet.append(list(COLUMNS))
    def add(inv,code,q,p,cid,day,sheet='Year 2009-2010'):
        wb[sheet].append([inv,code,'Alpha product' if code=='A' else 'Beta product',q,
                          datetime(2010,1,day),p,cid,'UK'])
    add('I1','A',2,3,'SYNTHETIC-PRIVATE-C1',1)
    add('I1','B2',1,4,'SYNTHETIC-PRIVATE-C1',1)
    add('I2','A',2,3,'SYNTHETIC-PRIVATE-C1',10)
    add('I2','B2',1,4,'SYNTHETIC-PRIVATE-C1',10)
    add('I3','A',1,2,'SYNTHETIC-PRIVATE-C2',5)
    add('I4','B2',1,4,None,6)
    add('C5','A',1,3,'SYNTHETIC-PRIVATE-C1',1)
    add('I6','A',-1,3,None,1)
    add('I7','A',1,'bad',None,1)
    add('I1','A',1,3,'SYNTHETIC-PRIVATE-C1',1,'Year 2010-2011')
    with xlsx.open('xb') as file: wb.save(file)
    wb.close()
    data=directory/'synthetic_transactions.jsonl'
    jsonl_new(data,[{'InvoiceNo':i,'items':items} for i,items in
                    [('I1',['A','B2']),('I2',['A','B2']),('I3',['A']),('I4',['B2'])]])
    sha=file_hash(data)
    rules=directory/'synthetic_rules.jsonl'
    jsonl_new(rules,[{'schema_version':'1.0','rule_id':rule_id(['A'],['B2'],sha),
        'antecedents':['A'],'consequents':['B2'],'support_count':2,
        'antecedent_support_count':3,'consequent_support_count':3,'n_transactions':4,
        'support':.5,'confidence':2/3,'lift':8/9,'balanced_score':0,
        'min_support':'0.5','min_confidence':'0.5','min_lift':'0','source_dataset_hash':sha}])
    return xlsx,data,rules


def execute(args,run):
    for directory in ['data/public','data/internal','charts','reports','tests/internal','metadata/internal']:
        (run/directory).mkdir(parents=True)
    with (run/'.gitignore').open('x',encoding='utf-8') as f:
        f.write('data/internal/\nmetadata/internal/\ntests/internal/\n')
    with (run/'data/internal/NOT_FOR_PUBLICATION.txt').open('x',encoding='utf-8') as f:
        f.write('INTERNAL ONLY. Customer-level pseudonyms, linkage and source audit details; do not publish or upload. Raw Customer ID is not exported.\n')
    before=preserve_snapshot(run)
    write_json(run/'metadata/original_file_hashes.json',before)
    env=dict(os.environ)
    env['CUSTOMER_TEST_ROOT']=str(run/'tests/internal/fixtures')
    cmd=[sys.executable,'-B','-m','unittest','discover','-s','tests','-v']
    tests=subprocess.run(cmd,cwd=ROOT,env=env,capture_output=True,text=True,
                         encoding='utf-8',errors='replace',timeout=90)
    with (run/'tests/unit_test.log').open('x',encoding='utf-8') as f:
        f.write(tests.stdout+tests.stderr)
    if tests.returncode: raise RuntimeError('Unit tests failed; no complete/partial-success claim')
    free_guard(args.min_free_mib)
    if args.smoke_test:
        args.xlsx,args.data,args.rules=smoke_fixture(run)
    baskets,invoices,sha=load_transactions(args.data)
    raw=args.data.read_bytes()
    lf_sha=hashlib.sha256(raw.replace(b'\r\n',b'\n')).hexdigest()
    del raw
    if not args.smoke_test and sha!='ad35aa8fc91b0ac3b4e249d476124f970dbb9c088a4b71badd857f1c9b2fe7da':
        if lf_sha!='82af1569237c6973f24a2992792fb78bb893c85fe245ec4ffb3dba663d8cc38e':
            raise ValueError('Dataset content differs from verified benchmark data')
    if len(set(invoices))!=len(invoices): raise ValueError('Duplicate benchmark invoice identifiers')
    overview,products,size_counts=basket_analysis(baskets)
    # Independent source JSONL reader: does not call basket_analysis/load_transactions.
    raw_rows=read_rows(args.data)
    independent_products=Counter()
    independent_sizes=Counter()
    for r in raw_rows:
        if len(r['items'])!=len(set(r['items'])): raise AssertionError('Repeated products inside original basket')
        independent_products.update(r['items'])
        independent_sizes[len(r['items'])]+=1
    if products!=independent_products or size_counts!=independent_sizes or len(raw_rows)!=len(baskets):
        raise AssertionError('Independent basket counts differ')
    del raw_rows
    invoice_baskets=dict(zip(invoices,baskets))
    xlsx,search=find_excel(args.xlsx)
    source_available=xlsx is not None
    source_error=None
    linked=[]
    votes={}
    source_check={'status':'not_verified_missing_excel'}
    if xlsx is not None:
        xlsx_hash=file_hash(xlsx)
        try:
            groups,votes,quality=audit_source(read_excel(xlsx),max_rows=args.max_source_rows,
                memory_guard=lambda:free_guard(args.min_free_mib))
            source_check,overlap_proofs=independent_source_check(xlsx,quality,groups)
            write_json(run/'data/internal/worksheet_copy_proofs.json',overlap_proofs)
            linked,link_quality=link_transactions(invoice_baskets,groups,
                overlap_proofs if args.resolve_verified_overlaps else None)
            quality['name_votes_removed_for_proven_sheet_copies']=collapse_copy_name_votes(votes,linked)
            quality['xlsx_sha256']=xlsx_hash
            quality['n_cross_worksheet_invoice_codes']=sum(c>1 for c in Counter(k[1] for k in groups).values())
            del groups
        except Exception as exc:
            source_error=f'{type(exc).__name__}: {exc}'
            votes={}
            source_available=False
            quality={'status':'source_analysis_failed','xlsx_sha256':xlsx_hash,'error':source_error,
                     'source_rows':None,'valid_rows':None}
            linked=[]
            source_check={'status':'failed','error':source_error}
    else:
        xlsx_hash=None
        quality={'status':'not_available','source_rows':None,'valid_rows':None,
                 'worksheets':None,'xlsx_sha256':None,
                 'reason':'Original Excel not found; README counts are historical, not reverified'}
    if not linked:
        link_quality={'n_transactions':len(baskets),'verified_linked':0,'verified_coverage':0,
                      'status_counts':{'not_attempted_missing_or_invalid_excel':len(baskets)},
                      'link_failures_assessed':False,'cross_sheet_keys_never_merged':True}
    catalog=product_catalog(products,votes,source_available=source_available,min_votes=args.min_name_votes)
    catalog_by_code={r['stock_code']:r for r in catalog}
    customers,monthly,frequency=customer_behavior(linked)
    rfm_rows,rfm_summary=rfm(linked)
    rfm_summary['real_data_verified']=bool(source_available and source_check.get('status')=='passed' and not args.smoke_test)
    if not source_available:
        rfm_summary.update(status='not_available',n_customers=None,eligible_invoices=None,
                           reason='No verified dates/customer IDs/monetary values')
    else: rfm_summary['status']='computed_on_eligible_linked_subset'
    # Only internal artifacts contain customer-level pseudonyms. No raw IDs exported.
    key=secrets.token_bytes(32)
    write_json(run/'metadata/internal/privacy_key.json',{'hmac_sha256_key_hex':key.hex(),
               'warning':'INTERNAL SECRET. Never publish. Tokens are not claimed anonymous.'})
    def safe_internal(row):
        out=dict(row)
        cid=out.pop('customer_id',None)
        out['customer_key']=pseudonym(cid,key) if cid else None
        return out
    jsonl_new(run/'data/internal/transaction_links.jsonl',[safe_internal(r) for r in linked])
    jsonl_new(run/'data/internal/customers.jsonl',[safe_internal(r) for r in customers])
    jsonl_new(run/'data/internal/rfm.jsonl',[safe_internal(r) for r in rfm_rows])
    rules_path=choose_rules(args.rules,sha)
    rules_hash=file_hash(rules_path)
    rules=read_rows(rules_path)
    if len({r['rule_id'] for r in rules})!=len(rules): raise ValueError('Duplicate rule IDs in source catalog')
    enriched=[]
    for rule in rules:
        if rule['source_dataset_hash']!=sha or rule['n_transactions']!=len(baskets):
            raise ValueError('Rule dataset fingerprint/transaction count differs')
        if any(code not in products for side in ['antecedents','consequents'] for code in rule[side]):
            raise ValueError('Unknown StockCode in rule')
        result=enrich_rule(rule,catalog_by_code,rules_hash)
        if any(result[k]!=v for k,v in rule.items()): raise AssertionError('Original rule metrics changed')
        enriched.append(result)
    fully_named=sum(all(p['description'] for side in ['antecedents','consequents']
                        for p in r[side+'_products']) for r in enriched)
    no_names=sum(not any(p['description'] for side in ['antecedents','consequents']
                         for p in r[side+'_products']) for r in enriched)
    name_quality={'n_products':len(catalog),'n_reliable_names':sum(r['reliable_name'] for r in catalog),
        'n_products_with_multiple_descriptions':sum(r['n_descriptions']>1 for r in catalog) if source_available else None,
        'source_available_for_name_quality':source_available,
        'name_status_counts':dict(Counter(r['name_status'] for r in catalog)),
        'name_mapping_coverage':sum(r['reliable_name'] for r in catalog)/len(catalog) if catalog else None,
        'n_rules_source':len(rules),'n_rules_exported_with_name_fields':len(enriched),
        'n_rules_fully_named':fully_named,'n_rules_partially_named':len(rules)-fully_named-no_names,
        'n_rules_without_names':no_names,'all_rule_codes_in_catalog':True,
        'all_original_rule_fields_unchanged':True,
        'selection':'NFKC, whitespace collapse, uppercase; distinct (worksheet,invoice) votes per description; '
                    'highest votes then lexical; reliable only unique winner with >=min_votes and >=50% of name votes',
        'min_votes':args.min_name_votes}
    amount_values=[float(r['order_value']) for r in linked if r.get('order_value') is not None]
    overview.update(n_known_customers=len(customers) if source_available else None,
                    order_value=descriptive(amount_values) if source_available else None,
                    n_invoices_with_order_value=len(amount_values) if source_available else None,
                    n_customer_ids_missing=sum(r.get('customer_id') is None for r in linked if r.get('link_status')=='verified')
                        if source_available else None)
    product_rows=[{'stock_code':r['stock_code'],'description':r['description'],
        'name_status':r['name_status'],'invoice_count':r['invoice_count'],
        'invoice_support':r['invoice_count']/len(baskets)} for r in catalog]
    product_rows.sort(key=lambda r:(-r['invoice_count'],r['stock_code']))
    public=run/'data/public'
    write_json(public/'product_catalog.json',catalog)
    csv_new(public/'product_catalog.csv',catalog,list(catalog[0]) if catalog else ['stock_code','description'])
    write_json(public/'product_names.json',{r['stock_code']:r['description'] for r in catalog})
    write_json(public/'overview.json',overview)
    csv_new(public/'product_popularity.csv',product_rows,
            ['stock_code','description','name_status','invoice_count','invoice_support'])
    size_rows=[{'distinct_products':k,'invoices':v} for k,v in sorted(size_counts.items())]
    csv_new(public/'basket_sizes.csv',size_rows,['distinct_products','invoices'])
    csv_new(public/'monthly.csv',monthly,['month','invoices','known_active_customers','gross_value_eligible','invoices_with_value'])
    csv_new(public/'customer_frequency_distribution.csv',
            [{'invoices_per_customer':k,'customers':v} for k,v in sorted(frequency.items())],
            ['invoices_per_customer','customers'])
    segment_rows=[{'segment':k,'customers':v} for k,v in rfm_summary['segments'].items()]
    csv_new(public/'rfm_segments.csv',segment_rows,['segment','customers'])
    jsonl_new(public/'recommendation_rules_enriched.jsonl',enriched)
    top=top_rules(enriched,'balanced',50) if enriched else []
    table=[]
    for r in enriched:
        def side_text(side):
            return ' + '.join(p['description']+' ['+p['stock_code']+']' if p['description']
                             else p['stock_code']+' [chưa có tên]' for p in r[side+'_products'])
        table.append({'rule_id':r['rule_id'],'antecedents':r['antecedents'],'consequents':r['consequents'],
            'antecedent_display':side_text('antecedents'),'consequent_display':side_text('consequents'),
            'support_count':r['support_count'],'support':r['support'],'confidence':r['confidence'],'lift':r['lift']})
    fields=['rule_id','antecedents','consequents','antecedent_display','consequent_display','support_count','support','confidence','lift']
    csv_new(public/'rules_readable.csv',table,fields)
    selected_ids={r['rule_id'] for r in top}
    csv_new(public/'top_rules_readable.csv',[r for r in table if r['rule_id'] in selected_ids],fields)
    # New schema extends, rather than mislabels, the strict phase-2 schema.
    base_schema_path=rules_path.parent/'rule_schema.json'
    schema=read_json(base_schema_path) if base_schema_path.exists() else {'type':'object','properties':{},'required':[]}
    schema['title']='Association Rule + Product Enrichment v1.0'
    for side in ['antecedents','consequents']:
        schema['properties'][side+'_products']={'type':'array','items':{'type':'object','required':['stock_code','description','name_status'],
            'properties':{'stock_code':{'type':'string'},'description':{'type':['string','null']},'name_status':{'type':'string'}}}}
        schema['required'].append(side+'_products')
    schema['properties']['enrichment']={'type':'object'}
    schema['required'].append('enrichment')
    write_json(public/'enriched_rule_schema.json',schema)
    verify_public([catalog,overview,product_rows,monthly,segment_rows,enriched])
    validation={'unit_tests':{'passed':True,'command':cmd,'returncode':tests.returncode},
        'basket_independent_recount':'passed','source_independent_validation':source_check,
        'all_original_rule_fields_unchanged':True,'all_rule_codes_resolve_to_catalog':True,
        'public_structured_identifier_scan':'passed','raw_customer_ids_exported':False,
        'real_excel_rfm_validation':'unavailable' if not source_available else 'eligible-subset values; mathematical RFM validated on hand fixtures',
        'not_validated_on_real_source':[] if source_available else ['source rows','cleaned rows','Description mapping',
            'InvoiceDate','Customer ID','Country','order value','customer behavior','monthly activity','RFM']}
    write_json(run/'tests/validation_report.json',validation)
    write_json(run/'reports/data_quality.json',quality)
    write_json(run/'reports/transaction_linkage.json',link_quality)
    write_json(run/'reports/catalog_quality.json',name_quality)
    write_json(run/'reports/rfm_method_and_summary.json',rfm_summary)
    free_guard(args.min_free_mib)
    charts=make_charts(run,product_rows,size_counts,top,monthly,frequency,rfm_summary,amount_values)
    source_changed=xlsx is not None and file_hash(xlsx)!=xlsx_hash
    if source_changed: raise AssertionError('Excel changed during read-only analysis')
    if args.smoke_test:
        assert quality['source_rows']==10 and quality['valid_rows']==7
        assert link_quality['verified_linked']==3 and len(customers)==2
        assert name_quality['n_reliable_names']==2 and fully_named==1
        assert rfm_summary['reference_date']=='2010-01-11'
        assert sum(Decimal(r['gross_value_eligible']) for r in monthly)==Decimal(16)
    status=('partial_source_validation_failed' if source_error else 'partial_missing_excel' if not source_available
            else 'partial_linkage' if link_quality['verified_linked']!=len(baskets) else 'completed_with_quality_limits')
    metadata={'created_utc':utc_now(),'status':status,'mode':'synthetic_smoke' if args.smoke_test else 'real',
        'data_path':str(args.data.resolve()),'data_sha256':sha,'data_lf_sha256':lf_sha,
        'xlsx_path':str(xlsx) if xlsx else None,'xlsx_sha256':xlsx_hash,'xlsx_search':search,
        'rules_path':str(rules_path),'rules_sha256':rules_hash,'git':git_info(),
        'source_sha256':{str(p.relative_to(ROOT)):file_hash(p) for p in sorted((ROOT/'src').rglob('*.py'))},
        'environment':{'python':sys.version,'platform':platform.platform(),'psutil':psutil.__version__,
            'logical_cpus':psutil.cpu_count(),'ram_bytes':psutil.virtual_memory().total},
        'method':{'worksheet_copies':'Optional explicit proof: all eight raw columns and row multiplicities equal in independent second pass, metadata agrees; select one copy, never sum copies; collapse name votes',
            'monetary':'Positive retained source lines only, invoices with any exact duplicate excluded from monetary/RFM; no claim of net revenue',
            'currency':'Price-source units; currency not independently verified',
            'rfm':'Next day after latest verified linked date; eligible known-customer invoices only; midrank quintiles, ties unsplit',
            'privacy':'No raw Customer ID exported. Customer-level pseudonyms/internal HMAC key remain INTERNAL and gitignored locally'},
        'configuration':{k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()}}
    write_json(run/'metadata/run.json',metadata)
    summary={'status':status,'source_rows':quality['source_rows'],'valid_source_rows':quality['valid_rows'],
        'n_transactions':len(baskets),'n_verified_linked':link_quality['verified_linked'],
        'n_products':len(products),'n_products_with_reliable_names':name_quality['n_reliable_names'],
        'n_known_customers':overview['n_known_customers'],'n_rules':len(enriched),'n_rules_fully_named':fully_named,
        'n_rules_partially_named':name_quality['n_rules_partially_named'],'data_sha256':sha}
    write_json(run/'reports/summary.json',summary)
    csv_new(run/'reports/summary.csv',[summary],list(summary))
    write_report(run,args,summary,overview,name_quality,link_quality,rfm_summary,product_rows,top,charts)
    after=preserve_snapshot(run)
    changed=[name for name,digest in before.items() if after.get(name)!=digest]
    write_json(run/'metadata/preservation_check.json',{'checked_files':len(before),'changed_or_missing':changed,'passed':not changed})
    if changed: raise AssertionError('Existing files changed')
    write_json(run/'completion.json',{'status':status,'safe_available_work_finished':True,
        'all_requested_real_source_analysis_completed':status=='completed_with_quality_limits','utc':utc_now()})
    print(json.dumps(summary,ensure_ascii=False),flush=True)


def make_charts(run,products,sizes,rules,monthly,frequency,rfm_summary,amounts):
    os.environ['MPLCONFIGDIR']=str(run/'metadata/matplotlib_config')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10})
    available=[]
    def save(fig,name,data,fields):
        fig.savefig(run/f'charts/{name}.png',dpi=200,bbox_inches='tight')
        plt.close(fig)
        csv_new(run/f'charts/{name}_data.csv',data,fields)
        available.append(name)
    selected=products[:20]
    fig,ax=plt.subplots(figsize=(10,7),layout='constrained')
    labels=[(r['description'][:45]+' ['+r['stock_code']+']') if r['description'] else r['stock_code']+' [chưa có tên]' for r in selected]
    ax.barh(labels[::-1],[r['invoice_count'] for r in selected[::-1]],color='#16828b')
    ax.set_xlim(left=0)
    ax.set_xlabel('Số hóa đơn chứa StockCode (không phải số lượng bán)')
    ax.set_title('Top sản phẩm phổ biến theo giao dịch')
    save(fig,'top_products',selected,['stock_code','description','invoice_count','invoice_support'])
    bins=[('1',1,1),('2–5',2,5),('6–10',6,10),('11–20',11,20),('21–50',21,50),('51–100',51,100),('101–200',101,200),('>200',201,float('inf'))]
    data=[{'basket_size_bin':label,'invoices':sum(v for k,v in sizes.items() if low<=k<=high)} for label,low,high in bins]
    fig,ax=plt.subplots(figsize=(9,5),layout='constrained')
    bars=ax.bar([r['basket_size_bin'] for r in data],[r['invoices'] for r in data],color='#6753a5')
    ax.bar_label(bars,fontsize=9,padding=3)
    ax.set_ylim(0,max(r['invoices'] for r in data)*1.2)
    ax.set_xlabel('Số sản phẩm khác nhau mỗi hóa đơn (nhóm cuối giữ toàn bộ đuôi dài)')
    ax.set_ylabel('Số hóa đơn')
    ax.set_title('Phân bố quy mô giỏ hàng')
    save(fig,'basket_sizes',data,['basket_size_bin','invoices'])
    if rules:
        chosen=[]
        seen=set()
        for r in rules:
            key=tuple(sorted(r['antecedents']+r['consequents']))
            if key not in seen: chosen.append(r);seen.add(key)
            if len(chosen)==5: break
        data=[{'rule_id':r['rule_id'],'rule':' + '.join(r['antecedents'])+' → '+' + '.join(r['consequents']),
               'support_percent':r['support']*100,'confidence_percent':r['confidence']*100,'lift':r['lift']} for r in chosen]
        fig,axes=plt.subplots(1,3,figsize=(14,4.5),layout='constrained',sharey=True)
        for ax,metric,label in zip(axes,['support_percent','confidence_percent','lift'],['Support (%)','Confidence (%)','Lift (tỉ số)']):
            ax.barh([r['rule'] for r in data][::-1],[r[metric] for r in data][::-1],color='#16828b')
            ax.set_xlim(0,100 if metric=='confidence_percent' else max(r[metric] for r in data)*1.15)
            ax.set_xlabel(label)
        fig.suptitle('Luật tiêu biểu — giữ nguyên chỉ số Giai đoạn 2; nhãn là StockCode')
        save(fig,'association_rules_examples',data,['rule_id','rule','support_percent','confidence_percent','lift'])
    for name,metric,label in [('monthly_transactions','invoices','Số hóa đơn'),
                              ('monthly_gross_value','gross_value_eligible','Giá trị gộp đủ điều kiện (đơn vị Price nguồn)'),
                              ('monthly_active_customers','known_active_customers','Số khách có ID được xác định')]:
        if monthly:
            fig,ax=plt.subplots(figsize=(10,5),layout='constrained')
            ax.bar([r['month'] for r in monthly],[float(r[metric]) for r in monthly],color='#16828b')
            ax.set_ylim(bottom=0);ax.tick_params(axis='x',rotation=60);ax.set_ylabel(label)
            ax.set_title(label+' theo tháng — chỉ tập hóa đơn liên kết hợp lệ')
            save(fig,name,monthly,['month','invoices','known_active_customers','gross_value_eligible','invoices_with_value'])
    if frequency:
        data=[{'invoices_per_customer':k,'customers':v} for k,v in sorted(frequency.items())]
        fig,ax=plt.subplots(figsize=(9,5),layout='constrained')
        ax.bar([str(r['invoices_per_customer']) for r in data],[r['customers'] for r in data],color='#6753a5')
        ax.set_ylim(bottom=0);ax.set_xlabel('Số hóa đơn liên kết mỗi khách có ID');ax.set_ylabel('Số khách')
        ax.set_title('Phân bố tần suất mua — không gom các hóa đơn thiếu Customer ID')
        save(fig,'customer_frequency',data,['invoices_per_customer','customers'])
    if rfm_summary['segments']:
        data=[{'segment':k,'customers':v} for k,v in rfm_summary['segments'].items()]
        fig,ax=plt.subplots(figsize=(9,5),layout='constrained')
        ax.barh([r['segment'] for r in data],[r['customers'] for r in data],color='#16828b')
        ax.set_xlim(left=0);ax.set_xlabel('Số khách đủ điều kiện RFM');ax.set_title('Nhóm điểm RFM — phân nhóm mô tả, chưa xác minh thực tế')
        save(fig,'rfm_segments',data,['segment','customers'])
    if amounts:
        fig,ax=plt.subplots(figsize=(9,5),layout='constrained')
        values,edges,_=ax.hist(amounts,bins=30,color='#16828b')
        ax.set_xlim(left=0);ax.set_ylim(bottom=0);ax.set_xlabel('Giá trị hóa đơn (đơn vị Price nguồn)');ax.set_ylabel('Số hóa đơn đủ điều kiện')
        ax.set_title('Phân bố giá trị gộp — không phải doanh thu ròng')
        data=[{'lower':float(a),'upper':float(b),'invoices':int(n)} for a,b,n in zip(edges[:-1],edges[1:],values)]
        save(fig,'order_value',data,['lower','upper','invoices'])
    write_json(run/'charts/manifest.json',{'available':available,
        'not_available':[n for n in ['monthly_transactions','monthly_gross_value','monthly_active_customers',
            'customer_frequency','rfm_segments','order_value'] if n not in available],
        'reason_for_missing':'Requires verified source dates, customers and/or monetary values',
        'format':'PNG 200 dpi, chart source data saved alongside'})
    return available


def write_report(run,args,summary,overview,names,linkage,rfm_summary,products,rules,charts):
    lines=['# Giai đoạn 3 — Làm giàu sản phẩm và phân tích hành vi khách hàng','',
        '**Trạng thái: '+('HOÀN THÀNH MỘT PHẦN' if summary['status'].startswith('partial') else 'HOÀN THÀNH VỚI GIỚI HẠN CHẤT LƯỢNG')+'**',
        '', '**Chế độ: '+('DỮ LIỆU TỔNG HỢP ĐỂ KIỂM THỬ, KHÔNG PHẢI KẾT QUẢ ONLINE RETAIL II' if args.smoke_test else 'DỮ LIỆU THẬT HIỆN CÓ TRONG PROJECT')+'**','',
        '## 1. Mục tiêu, phạm vi và kết quả có thể xác minh','',
        'Mục tiêu là liên kết hóa đơn, chuẩn hóa tên sản phẩm, phân tích giỏ hàng/khách hàng và làm giàu luật. '
        'Phân biệt số liệu đo trực tiếp, phương pháp đã kiểm thử trên mẫu, và hạng mục chưa có dữ liệu. '
        'Không chạy lại khai thác hoặc benchmark; không xây website trong giai đoạn này.', '',
        '| Hạng mục | Kết quả |','|---|---:|']
    for label,key in [('Dòng Excel nguồn','source_rows'),('Dòng nguồn hợp lệ','valid_source_rows'),
        ('Giao dịch hiện có','n_transactions'),('Giao dịch xác minh liên kết','n_verified_linked'),
        ('StockCode trong dataset','n_products'),('StockCode có tên đáng tin cậy','n_products_with_reliable_names'),
        ('Khách hàng xác định được','n_known_customers'),('Luật xuất kèm trường tên','n_rules'),
        ('Luật có đủ tên thật','n_rules_fully_named'),('Luật có một phần tên thật','n_rules_partially_named')]:
        value=summary[key]
        lines.append(f'| {label} | {"Chưa xác minh / thiếu nguồn" if value is None else f"{value:,}".replace(",",".")} |')
    lines += ['', '## 2. Dữ liệu đầu vào và kiểm tra nguồn','',
        f'- SHA256 giao dịch thực tế: `{summary["data_sha256"]}`. Hash chuẩn hóa LF nằm trong metadata; không ghi lại file gốc.',
        '- Tìm Excel đúng tên trong thư mục dữ liệu và root của project; không dùng workbook tổng hợp trong results/tests như nguồn thật. '
        'Không tìm bên ngoài phạm vi project hoặc tải nguồn mới.',
        '- Nếu thiếu Excel: SHA256 Excel, số dòng hai worksheet, dòng hợp lệ, thiếu/trùng, phạm vi ngày, '
        'Customer ID, Country và Price đều **chưa thể kiểm chứng trên nguồn thật**. '
        'Số 1.037.032 dòng trong README chỉ là thông tin lịch sử, không thay thế kết quả kiểm tra hiện tại.',
        '- `reports/data_quality.json` ghi trạng thái và lý do; `metadata/run.json` ghi đường dẫn tìm kiếm, hash, '
        'phiên bản Python/thư viện/source, Git status và phương pháp.', '',
        '## 3. Quy tắc làm sạch và liên kết','',
        'Bộ lọc giữ nguyên nguyên tắc pipeline cũ: bỏ Invoice bắt đầu C không phân biệt hoa thường; '
        'Quantity>0; Price>0; loại đúng tập mã B, ADJUST, ADJUST2, AMAZONFEE, BANK CHARGES, C2, DOT, POST, D, 23444, M, m. '
        'Kiểm thử đối chiếu tập mã này trực tiếp với AST của script gốc. Không dựng lại transactions_v1.', '',
        'Bổ sung kiểm soát: số không hữu hạn, thiếu Invoice hoặc StockCode bị loại và ghi rõ. '
        'Các trường date/customer/country thiếu không làm mất sản phẩm khỏi giỏ nhưng không được suy diễn metadata. '
        'Customer ID thiếu không trở thành một khách hàng chung; không suy ra ID từ Invoice hay sản phẩm.', '',
        'Nhóm dòng theo (worksheet, Invoice), mặc định chỉ liên kết khi Invoice có một khóa nguồn và giỏ khớp chính xác. '
        'Nếu bật --resolve-verified-overlaps, chỉ chọn một bản sao khi lần đọc độc lập chứng minh đa tập dòng của '
        'toàn bộ tám cột bằng nhau, kể cả số lần lặp, và mọi metadata/tổng tiền thống nhất. Thứ tự dòng có thể khác. '
        'Giữ cả khóa nguồn để truy vết, không cộng dòng hai sheet; các trường hợp không đủ bằng chứng vẫn ambiguous. '
        'Phiếu chọn tên cũng chỉ tính một lần cho hóa đơn đã được chứng minh là bản sao. '
        'Metadata chỉ nhận giá trị khi mọi dòng phù hợp thống nhất và không thiếu.', '',
        f'Đã xác minh {linkage["verified_linked"]}/{summary["n_transactions"]} giao dịch. '
        'Khi thiếu Excel, đây là độ phủ liên kết đã xác minh bằng 0, **không phải tỷ lệ sai của thuật toán liên kết**; '
        'các hóa đơn được đánh dấu chưa thử, không đánh dấu ghép sai.', '',
        'Dòng trùng chính xác được đếm riêng trong mỗi sheet; không xóa dòng gốc. Số tiền từ mọi dòng giữ lại '
        'và số tiền sau bỏ dòng trùng để phân tích độ nhạy cùng được lưu. Hóa đơn có dòng trùng bị loại khỏi '
        'Monetary/RFM thay vì mặc định biết dòng trùng là lỗi hay giao dịch thật. Chỉ dùng tổng Quantity×Price '
        'đủ điều kiện; đây là giá trị gộp của hàng giữ lại, không phải doanh thu ròng sau hủy/hoàn/thuế/phí. '
        'Đơn vị tiền tệ chưa được xác minh độc lập.', '',
        '## 4. Product Catalog và chất lượng ánh xạ','',
        'StockCode là chuỗi nguyên gốc sau trim, giữ số 0 đầu và phân biệt chữ hoa/thường. Description '
        'chuẩn hóa NFKC, gộp khoảng trắng, viết hoa; loại giá trị rỗng/placeholder, không bịa tên. '
        'Một mô tả có một phiếu trên mỗi (worksheet, Invoice); lặp dòng không làm tăng phiếu. '
        'Chọn mô tả nhiều phiếu nhất, hòa thì chọn từ điển để tái lập nhưng không coi là tên đáng tin cậy. '
        f'Tên dùng cho demo cần ít nhất {args.min_name_votes} phiếu, thắng duy nhất và đạt ≥50% tổng phiếu mô tả. '
        'Đây là tiêu chí chất lượng tên quan sát được, không phải nhãn chuyên gia xác minh.', '',
        f'Catalog phủ {names["n_products"]} StockCode trong dataset; tên đáng tin cậy: {names["n_reliable_names"]} '
        f'({names["name_mapping_coverage"]*100:.2f}%). Thiếu nguồn thì Description=null, name_status=missing_source; '
        'trường hiển thị fallback StockCode không được gọi là tên sản phẩm. '
        'Chi tiết biến thể tên, phiếu và trạng thái có trong product_catalog và catalog_quality.json.', '',
        '## 5. Đặc điểm giao dịch quan sát được','',
        f'- Trung bình sản phẩm khác nhau/giỏ: {overview["basket_size"]["mean"]:.4f}.',
        f'- Trung vị: {overview["basket_size"]["median"]}; p95 nội suy: {overview["basket_size"]["p95"]:.2f}.',
        f'- Giỏ một sản phẩm: {overview["singleton_baskets"]}; giỏ rỗng: {overview["empty_baskets"]}; '
        f'giỏ lớn nhất: {overview["basket_size"]["max"]}.',
        '- Các giỏ lớn có thể ảnh hưởng luật; chưa có dữ liệu để khẳng định đó là khách sỉ. Không loại outlier trong phân tích này.', '',
        '| StockCode | Số hóa đơn chứa mã | Tỉ lệ giao dịch | Tên đã xác minh theo quy tắc |',
        '|---|---:|---:|---|']
    for r in products[:10]:
        lines.append(f'| {r["stock_code"]} | {r["invoice_count"]} | {100*r["invoice_support"]:.2f}% | {r["description"] or "Chưa có tên"} |')
    lines += ['', 'Phổ biến tính theo số hóa đơn chứa mã, không phải số lượng bán hoặc doanh thu. '
        'Hóa đơn không đồng nghĩa khách hàng; không dùng phân bố giỏ hàng thay cho tần suất mua của người mua.', '',
        '## 6. Khách hàng, thời gian và RFM','',
        'Các phân tích cần metadata nguồn (số khách, số lần mua/khách, tổng giá trị và số SKU/khách, '
        'tháng mua, khách hoạt động và RFM) chỉ được thực hiện trên tập hóa đơn liên kết đủ điều kiện. '
        'Nếu nguồn thiếu, trường kết quả là null hoặc tệp chỉ có header, kèm trạng thái unavailable; '
        'không trình bày đồ thị rỗng như dữ liệu đo được bằng 0.', '',
        f'RFM hiện có trạng thái `{rfm_summary["status"]}`; ngày tham chiếu: `{rfm_summary["reference_date"]}`. '
        'Phương pháp đã triển khai: ngày sau ngày mua muộn nhất đã liên kết, không dùng ngày hiện tại; '
        'Recency là số ngày từ lần mua cuối đủ điều kiện, Frequency là số hóa đơn đủ điều kiện, '
        'Monetary là tổng giá trị đủ điều kiện. Kết quả là phân tích tập con, cần đọc cùng độ phủ liên kết, thiếu ID và giá trị.', '',
        'Điểm 1–5 dùng percentile midrank=(số nhỏ hơn+0,5×số bằng)/N; điểm=min(5,1+floor(5×percentile)), '
        'đảo chiều cho Recency. Giá trị bằng nhau không bị chia nhóm; toàn bộ bằng nhau nhận điểm 3. '
        'Nhóm điểm cao: cả R/F/M≥4; tần suất cao lâu chưa mua: R≤2,F≥4; gần đây tần suất thấp: R≥4,F≤2; '
        'lâu chưa mua tần suất thấp: R≤2,F≤2; còn lại trung gian. Đây là phân nhóm mô tả, chưa xác minh là phân khúc thực tế.', '',
        '## 7. Làm giàu và ứng dụng luật kết hợp','',
        f'Đã xuất {summary["n_rules"]} luật có trường liên kết catalog, nhưng chỉ {summary["n_rules_fully_named"]} '
        'luật có đủ tên thật. Không đánh đồng thêm cấu trúc enrichment với đã bổ sung tên thành công. '
        'Mọi trường gốc, gồm rule_id, hai vế, support/count, confidence, lift, balanced score, ngưỡng và hash dataset '
        'được so bằng nhau trước/sau trên toàn bộ catalog. Hash file luật nguồn và source_rule_id giữ khả năng truy vết.', '',
        '| Luật theo StockCode | Số hóa đơn | Support | Confidence | Lift |','|---|---:|---:|---:|---:|']
    seen=set();count=0
    for r in rules:
        key=tuple(sorted(r['antecedents']+r['consequents']))
        if key in seen: continue
        seen.add(key)
        lines.append('| '+' + '.join(r['antecedents'])+' → '+' + '.join(r['consequents'])+
                     f' | {r["support_count"]} | {100*r["support"]:.2f}% | {100*r["confidence"]:.2f}% | {r["lift"]:.3f} |')
        count+=1
        if count==5: break
    lines += ['', 'Kết quả đo: các mã trong luật cùng xuất hiện ở số hóa đơn nêu trên. Suy luận có giới hạn: '
        'có thể đưa các quan hệ này vào danh sách ứng viên mua kèm để xem xét. Giả thuyết cần kiểm tra: '
        'hiển thị gợi ý có cải thiện mua hàng hay không. Không suy ra danh mục sản phẩm khi chưa có tên, '
        'không khẳng định quan hệ nhân quả hoặc hiệu quả hệ thống gợi ý ngoài mẫu.', '',
        '## 8. Biểu đồ và dữ liệu kiểm chứng','',
        'PNG 200 dpi, trục bắt đầu 0 hoặc bao phủ toàn bộ dữ liệu; giỏ đuôi dài được giữ trong nhóm >200. '
        'Mỗi biểu đồ có CSV nguồn tương ứng trong charts/. Các biểu đồ cần dữ liệu thiếu được đánh dấu '
        'not_available trong charts/manifest.json, không tự dựng số liệu.', '']
    for name in charts:
        lines += [f'### {name}','',f'![{name}](../charts/{name}.png)','']
    lines += ['## 9. Kiểm thử, kiểm chứng và riêng tư','',
        '- 19 test Giai đoạn 3 cộng 10 test hồi quy luật; dữ liệu tính tay, workbook tổng hợp hai sheet và đếm row XML độc lập. '
        'Bao gồm thiếu ID, giá/Quantity lỗi, tên nhiều biến thể/tie, hóa đơn trùng sheet, dòng trùng và doanh thu/điểm RFM.',
        '- Dataset thật được đọc lại bằng cách độc lập để kiểm tra số giỏ, số sản phẩm, tần suất SKU và phân bố kích thước. '
        'Nếu có Excel thì đọc lần hai với bộ lọc độc lập, so nhóm Invoice/tập SKU/tổng lượng/tổng giá trị và đếm row XML.',
        '- Tất cả trường luật gốc được giữ nguyên; public JSON được kiểm tra khóa định danh khách hàng và unit test canary. '
        'Không xuất Customer ID thô. Dữ liệu cấp khách hàng chỉ ở data/internal với HMAC pseudonym; khóa ở metadata/internal, '
        'không được coi là dữ liệu đã ẩn danh tuyệt đối. Các thư mục này được .gitignore cục bộ và đánh dấu INTERNAL.',
        '- Khi thiếu Excel, không khẳng định đã kiểm chứng dữ liệu khách hàng/RFM thật. tests/validation_report.json '
        'liệt kê riêng phần chưa kiểm chứng. Unit test thành công không làm trạng thái toàn giai đoạn thành hoàn thành.', '',
        '## 10. Hạn chế và kết luận','',
        'Có thể hoàn thành an toàn phân tích theo hóa đơn và chuẩn bị cấu trúc catalog/luật. Thiếu Excel ngăn '
        'việc hoàn tất ánh xạ tên và phân tích người mua/thời gian/giá trị; không thay bằng dữ liệu suy đoán. '
        'Ngay cả khi có nguồn, cần đánh giá độ phủ, thiếu ID, trùng dòng, hóa đơn nhập nhằng, phạm vi thời gian '
        'và cách tính giá trị trước khi diễn giải RFM. Chưa kiểm tra dự báo, ý nghĩa thống kê hoặc thay đổi theo mùa.', '',
        '## 11. Đề xuất Giai đoạn 4','',
        'Có thể làm demo đọc catalog luật theo StockCode với nhãn thiếu tên rõ ràng, không đưa dữ liệu khách hàng lên demo. '
        'Trước khi triển khai demo có tên sản phẩm hoặc màn hình phân khúc khách hàng, cần bổ sung Excel gốc đáng tin cậy, '
        'chạy lại pipeline này vào một thư mục mới, kiểm chứng hash/liên kết và các hạng mục còn thiếu. '
        'Đánh giá gợi ý bằng chia dữ liệu theo thời gian và baseline phổ biến trước khi khẳng định hiệu quả.', '',
        '## 12. Tệp bàn giao và cách sử dụng','',
        '- reports/summary.json và summary.csv: thống kê chính; data_quality, transaction_linkage, catalog_quality, rfm_method_and_summary: chất lượng/phương pháp.',
        '- data/public/: catalog, bảng tần suất, giỏ, tháng, nhóm RFM và luật enrichment cùng schema mới. '
        'Tệp customer/monthly không có số liệu thật khi thiếu nguồn; không hiểu header trống là 0 khách.',
        '- data/internal/, metadata/internal/, tests/internal/: KHÔNG CÔNG KHAI. Workbook ở tests/internal chỉ là mẫu tổng hợp, không phải Online Retail II.',
        '- tests/unit_test.log và validation_report.json; metadata/run.json và preservation_check.json; charts/manifest.json và CSV nguồn.', '',
        '```powershell','python -B src/validation/run_customer_analysis.py --smoke-test',
        'python -B src/validation/run_customer_analysis.py',
        'python -B src/validation/run_customer_analysis.py --xlsx data/raw/online_retail_II.xlsx','```', '',
        'Mỗi lần tạo run_id mới; không xóa, ghi đè, commit/push hoặc đổi branch. '+
        'Cần file Excel gốc để hoàn tất phần chưa có dữ liệu; không cần dựng lại dataset benchmark.', '']
    with (run/'reports/report.md').open('x',encoding='utf-8') as f: f.write('\n'.join(lines))


def main():
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--data',type=Path,default=ROOT/'data/processed/transactions_v1.jsonl')
    p.add_argument('--xlsx',type=Path)
    p.add_argument('--rules',type=Path)
    p.add_argument('--output-root',type=Path,default=ROOT/'results/customer_analysis')
    p.add_argument('--min-name-votes',type=int,default=2)
    p.add_argument('--max-source-rows',type=int,default=1200000)
    p.add_argument('--min-free-mib',type=int,default=128)
    p.add_argument('--smoke-test',action='store_true')
    p.add_argument('--resolve-verified-overlaps',action='store_true',
                   help='Select one copy only after independent full raw-row multiset proof; never merge different invoices')
    args=p.parse_args()
    if min(args.min_name_votes,args.max_source_rows,args.min_free_mib)<1:
        p.error('Resource limits and min-name-votes must be positive')
    args.output_root=args.output_root.resolve()
    args.output_root.mkdir(parents=True,exist_ok=True)
    run=args.output_root/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:12])
    run.mkdir()
    print('Run:',run,flush=True)
    try:
        execute(args,run)
    except (Exception,KeyboardInterrupt) as exc:
        write_json(run/'error.json',{'status':'failed_or_incomplete','error':f'{type(exc).__name__}: {exc}',
                                    'traceback':traceback.format_exc(),'utc':utc_now()})
        print(f'FAILED: {type(exc).__name__}: {exc}',file=sys.stderr)
        return 1
    print('Report:',run/'reports/report.md',flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
