"""Independent full-scan ranking audit + test execution; creates new QA files."""
import argparse
from collections import Counter
import csv
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
import os
from datetime import datetime, timezone
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))


def rows(path):
    with open(path,encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def digest(path):
    h = hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024**2),b''):
            h.update(block)
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('run',type=Path)
    a = p.parse_args()
    run = a.run.resolve()
    qa = run/'audit'/('qa_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8])
    qa.mkdir(exist_ok=False)
    started = time.perf_counter()
    meta = json.loads((run/'metadata.json').read_text(encoding='utf-8'))
    cfg = meta['config']
    # Third training recount from serialized data, no engine or popularity helper.
    baskets = rows(run/'internal/train.jsonl')
    counts = Counter(x for r in baskets for x in set(r['items']))
    assert counts == json.loads((run/'training_popularity.json').read_text(encoding='utf-8'))
    cases = rows(run/'internal/cases.jsonl')
    rules = rows(run/'training_first/rules.jsonl')
    train_digest = digest(run/'internal/train.jsonl')
    prepared = []
    for r in rules:
        n,u,al,cr = (r[k] for k in ('n_transactions','support_count','antecedent_support_count','consequent_support_count'))
        s,c,l = Fraction(u,n),Fraction(u,al),Fraction(u*n,al*cr)
        assert r['source_dataset_hash'] == train_digest
        key = (-s*c*max(0,1-1/l),-c,-l,-s,tuple(r['antecedents']),tuple(r['consequents']),r['rule_id'])
        prepared.append((set(r['antecedents']),set(r['consequents']),key))
    predictions = {}
    for case in cases:
        known = set(case['input'])
        candidate = {}
        # Independent exhaustive scan; production uses an antecedent index.
        for left,right,key in prepared:
            if all(x in known for x in left):
                for x in right:
                    if x not in known and (x not in candidate or key<candidate[x]):
                        candidate[x] = key
        ar = sorted(candidate,key=lambda x:(candidate[x],x))[:10]
        pop = [x for x,_ in sorted(counts.items(),key=lambda t:(-t[1],t[0])) if x not in known][:10]
        predictions[case['case_id']] = {'association_rules':ar,'popular':pop}
    checked = 0
    with open(run/'evaluation_results.csv',encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            assert json.loads(r['predicted']) == predictions[int(r['case_id'])][r['method']][:int(r['k'])]
            checked += 1
    # Independent chronological linkage check against actual source, not split helper.
    links = rows(Path(meta['phase3'])/'data/internal/transaction_links.jsonl')
    boundary = meta['split']['boundary_day']
    tr = [r for r in links if r.get('link_status')=='verified' and r.get('invoice_date') and r['invoice_date'][:10]<boundary]
    te = sorted([r for r in links if r.get('link_status')=='verified' and r.get('invoice_date') and r['invoice_date'][:10]>=boundary],key=lambda r:(r['invoice_date'],r['invoice']))
    assert len(tr)==meta['split']['train'] and len(te)==meta['split']['test']
    assert Counter(tuple(sorted(r['items'])) for r in tr)==Counter(tuple(r['items']) for r in baskets)
    for c in cases:
        assert not set(c['input'])&set(c['targets'])
        assert set(c['input'])|set(c['targets']) == set(te[c['case_id']]['items'])
    audit = {'status':'passed','ranking_prediction_rows_checked':checked,
             'cases_exhaustive_full_scan':len(cases),'training_popularity_independently_recounted':True,
             'chronological_split_independently_checked':True,'mask_partition_verified':True,
             'seconds':time.perf_counter()-started}
    with open(qa/'full_scan_oracle.json','x',encoding='utf-8') as f:
        json.dump(audit,f,indent=2)
    os.environ['RECOMMENDATION_EVALUATION_RUN'] = str(run)
    suite = unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_*.py')
    with open(qa/'unit_test.log','x',encoding='utf-8') as log:
        result = unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
    files = list((ROOT/'src/recommendation').glob('*.py'))+list((ROOT/'src/demo').glob('*.py'))+[
        ROOT/'src/validation/run_recommendation_evaluation.py',Path(__file__),
        ROOT/'tests/test_recommendation.py',ROOT/'tests/test_demo.py']
    import ast
    for f in files:
        ast.parse(f.read_text(encoding='utf-8'),filename=str(f))
    before = json.loads((run/'audit/preservation_before.json').read_text(encoding='utf-8'))
    changed = [rel for rel,sha in before.items() if digest(ROOT/rel)!=sha]
    test_report = {'status':'passed' if result.wasSuccessful() and not changed else 'failed',
        'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
        'skipped':len(result.skipped),'syntax_files':len(files),'full_scan_oracle':audit,
        'metric_oracle':json.loads((run/'audit/independent_metrics.json').read_text(encoding='utf-8')),
        'http_smoke':'real localhost HTTP endpoints and assets',
        'browser_visual_test':'not completed: Computer Use app approval timed out',
        'manual_test':False,'preserved_files':len(before),'changed_old_files':changed,
        'qa_directory':str(qa.relative_to(run)),
        'source_sha256_at_delivery':{str(f.relative_to(ROOT)):digest(f) for f in files},
        'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'git_status':subprocess.check_output(['git','status','--short'],cwd=ROOT,text=True).strip(),
        'no_commit_push':True}
    with open(run/'test_report.json','x',encoding='utf-8') as f:
        json.dump(test_report,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in test_report.items() if k not in ('git_status','source_sha256_at_delivery')},ensure_ascii=True))
    if not result.wasSuccessful() or changed:
        raise SystemExit(1)


if __name__=='__main__':
    main()
