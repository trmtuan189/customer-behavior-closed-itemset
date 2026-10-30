"""Generate, independently validate and report complete association rules.

python -B src/validation/run_association_rules.py --smoke-test
python -B src/validation/run_association_rules.py --supports 0.03 0.02 0.01 0.005

No miners/benchmarks are rerun. Real mode requires verified CHARM/FP-Growth
benchmark artifacts with the exact dataset SHA256 and transaction count.
Top-K limits ranking tables only; all filtered rules are exported.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import uuid

import psutil

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from rules.association_rules import (RULE_FIELDS, Thresholds, ResourceLimitError,
    recover_frequent, generate_rules, top_rules)
from association_rule_reference import (brute_force_itemsets, reference_rules,
    compare_rules, build_vertical_masks, recount_itemsets)
from benchmark_runner import file_hash, git_info, read_itemsets, write_json, utc_now
from common import load_transactions

CRITERIA = ('confidence','lift','support','balanced')


def jsonl_new(path, rows):
    with path.open('x',encoding='utf-8') as f:
        for row in rows:
            f.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')


def csv_new(path,rows,fields):
    import csv
    with path.open('x',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v
                        for k,v in row.items() if k in fields})


def protected_files(run):
    files=[ROOT/'README.md']
    for directory in ['src','tests','data','results']:
        files.extend(p for p in (ROOT/directory).rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts and not p.is_relative_to(run))
    return {str(p.relative_to(ROOT)):file_hash(p) for p in files}


def free_memory_guard(minimum):
    available=psutil.virtual_memory().available/1048576
    if available<minimum:
        raise ResourceLimitError(f'Available RAM {available:.1f} MiB below {minimum} MiB safety floor')


def discover_input(algorithm,support,sha,n,benchmarks):
    candidates=[]
    expected_source = ROOT/('src/validation/charm_impl.py' if algorithm=='charm'
                            else 'src/algorithms/fp_growth.py')
    for path in sorted(benchmarks.rglob('metadata.json')):
        meta=json.loads(path.read_text(encoding='utf-8'))
        if (meta.get('smoke_test') or meta.get('data_sha256')!=sha
                or meta.get('n_transactions')!=n
                or meta.get('configuration',{}).get('algorithms')!=[algorithm]):
            continue
        recorded=meta.get('source_sha256',{}).get(str(expected_source.relative_to(ROOT)))
        if recorded!=file_hash(expected_source):
            continue
        records_path=path.parent/'runs.jsonl'
        if not records_path.exists():
            continue
        rows=[json.loads(x) for x in records_path.read_text(encoding='utf-8').splitlines() if x.strip()]
        matching=[r for r in rows if r['algorithm']==algorithm and r['min_support']==support
                  and r['status']=='ok' and r['matches_reference'] is True
                  and r['data_sha256']==sha and r['n_transactions']==n]
        if matching:
            row=sorted(matching,key=lambda r:r['repeat'])[0]
            output=Path(row['run_dir'])/'itemsets.jsonl'
            candidates.append((str(path),row,output))
    if not candidates:
        raise ValueError(f'No verified {algorithm} artifact at {support} for this dataset/source')
    path,row,output=candidates[0]
    values={frozenset(k):v for k,v in read_itemsets(output).items()}
    if len(values)!=row['n_itemsets']:
        raise ValueError(f'Artifact itemset count changed: {output}')
    return values,{'algorithm':algorithm,'support':support,'metadata_path':path,
                   'metadata_sha256':file_hash(path),'itemsets_path':str(output),
                   'itemsets_sha256':file_hash(output),'worker_record':row}


def tiny_input(run,supports):
    transactions=[['A','B'],['A','B'],['A','B','C'],['A','C'],['C'],['A','B','C']]
    path=run/'smoke_transactions.jsonl'
    jsonl_new(path,[{'InvoiceNo':str(i),'items':t} for i,t in enumerate(transactions)])
    return path


def rule_schema():
    props={}
    for name in RULE_FIELDS:
        if name in ['antecedents','consequents']:
            props[name]={'type':'array','minItems':1,'uniqueItems':True,
                         'items':{'type':'string','minLength':1}}
        elif name in ['support_count','antecedent_support_count','consequent_support_count','n_transactions']:
            props[name]={'type':'integer','minimum':1}
        elif name in ['support','confidence']:
            props[name]={'type':'number','minimum':0,'maximum':1}
        elif name in ['lift','balanced_score']:
            props[name]={'type':'number','minimum':0}
        else:
            props[name]={'type':'string'}
    props['schema_version']['const']='1.0'
    props['rule_id']['pattern']='^ar_[0-9a-f]{64}$'
    props['source_dataset_hash']['pattern']='^[0-9a-f]{64}$'
    return {'$schema':'https://json-schema.org/draft/2020-12/schema',
            'title':'Customer Behavior Association Rule v1.0', 'type':'object',
            'additionalProperties':False,'required':list(RULE_FIELDS),'properties':props,
            '$comment':'Additional invariants validated by pipeline: disjoint nonempty sides; '
                       'counts <= N; union_count <= side counts; exact metrics from counts. '
                       'Decimal thresholds are canonical strings. rule_id is stable across supports '
                       'for the same byte-identical dataset and directed rule.'}


def execute(args,run):
    before=protected_files(run)
    write_json(run/'original_file_hashes.json',before)
    command=[sys.executable,'-B','-m','unittest','discover','-s','tests',
             '-p','test_association_rules.py','-v']
    tests=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,
                         encoding='utf-8',errors='replace',timeout=60)
    with (run/'unit_test.log').open('x',encoding='utf-8') as f:
        f.write(tests.stdout+tests.stderr)
    write_json(run/'unit_tests.json',{'command':command,'exit_code':tests.returncode,
                                    'randomized_configurations':360,'independent_oracle':True})
    if tests.returncode:
        raise RuntimeError('Unit tests failed; real data was not processed')
    free_memory_guard(args.min_free_mib)
    if args.smoke_test:
        args.data=tiny_input(run,args.supports)
    tx,invoices,sha=load_transactions(args.data)
    n=len(tx)
    if not n or len(set(invoices))!=n:
        raise ValueError('Empty dataset or duplicate invoice identifiers')
    raw=args.data.read_bytes()
    lf_sha=hashlib.sha256(raw.replace(b'\r\n',b'\n')).hexdigest()
    del raw,invoices
    write_json(run/'metadata.json',{'schema_version':'1.0','created_utc':utc_now(),
        'mode':'smoke' if args.smoke_test else 'real','data_path':str(args.data.resolve()),
        'source_dataset_hash':sha,'data_lf_sha256':lf_sha,'n_transactions':n,
        'n_products':len(set().union(*tx)),
        'git':git_info(),'source_sha256':{str(p.relative_to(ROOT)):file_hash(p)
                                       for p in sorted((ROOT/'src').rglob('*.py'))},
        'environment':{'python':sys.version,'platform':platform.platform(),
            'logical_cpus':psutil.cpu_count(),'total_ram_bytes':psutil.virtual_memory().total,
            'psutil':psutil.__version__,'mlxtend_available':bool(importlib.util.find_spec('mlxtend'))},
        'configuration':{k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},
        'method':'Complete frequent supports recovered from complete closed family; '
                 'all directed nonempty bipartitions; exact rational threshold comparisons.',
        'ranking':{'confidence':['confidence desc','lift desc','support desc'],
                   'lift':['lift desc','confidence desc','support desc'],
                   'support':['support desc','confidence desc','lift desc'],
                   'balanced':['support*confidence*max(0,1-1/lift) desc','lift desc','confidence desc','support desc'],
                   'all_ties':'antecedents tuple asc, consequents tuple asc; exact rational metric keys',
                   'top_k_scope':'Presentation only; no change to final rule counts'},
        'mapping_status':'No raw Excel/product descriptions in current repository; StockCode only',
        'completeness_scope':'All rules whose union meets each support and whose confidence/lift '
                             'meet configured inclusive thresholds; conditional on complete input.'})
    write_json(run/'rule_schema.json',rule_schema())
    write_json(run/'product_names.json',{})
    masks=build_vertical_masks(tx)
    del tx
    summaries,provenance,validation=[],[],[]
    combined=[]
    catalog={}
    for support in args.supports:
        free_memory_guard(args.min_free_mib)
        threshold=Thresholds(support,args.min_confidence,args.min_lift)
        if args.smoke_test:
            tiny=[['A','B'],['A','B'],['A','B','C'],['A','C'],['C'],['A','B','C']]
            frequent_reference,closed=brute_force_itemsets(tiny,support)
            origin={'support':support,'source':'Independent exhaustive tiny oracle'}
        else:
            closed,charm_origin=discover_input('charm',support,sha,n,args.benchmarks_root)
            frequent_reference,fp_origin=discover_input('fpgrowth',support,sha,n,args.benchmarks_root)
            closed_reference={frozenset(k):v for k,v in read_itemsets(
                ROOT/'results'/f"closed_reference_{support.replace('.', '_')}.jsonl").items()}
            if closed!=closed_reference:
                raise AssertionError(f'CHARM differs from saved closed reference at {support}')
            origin={'support':support,'closed':charm_origin,'frequent':fp_origin}
        frequent=recover_frequent(closed,n,threshold,max_frequent=args.max_frequent,
                                  max_subset_visits=args.max_subset_visits)
        if frequent!=frequent_reference:
            raise AssertionError(f'Closed reconstruction is incomplete or incorrect at {support}')
        recount=recount_itemsets(frequent,masks)
        rules,summary=generate_rules(frequent,n,threshold,sha,max_candidates=args.max_candidates)
        expected,counts=reference_rules(frequent_reference,n,args.min_confidence,args.min_lift)
        comparison=compare_rules(rules,expected)
        if any(summary[k]!=v for k,v in counts.items()):
            raise AssertionError('Independent candidate/filter counts differ')
        ids=[r['rule_id'] for r in rules]
        if len(set(ids))!=len(ids):
            raise AssertionError('Duplicate rule ID within support')
        for r in rules:
            assert set(r)==set(RULE_FIELDS)
            assert not set(r['antecedents'])&set(r['consequents'])
            assert r['support_count']<=min(r['antecedent_support_count'],r['consequent_support_count'])<=n
            previous=catalog.get(r['rule_id'])
            if previous:
                for field in ['antecedents','consequents','support_count','antecedent_support_count',
                              'consequent_support_count','support','confidence','lift']:
                    if previous[field]!=r[field]:
                        raise AssertionError('Same rule ID has different metrics across supports')
            if previous is None or Decimal(r['min_support'])<Decimal(previous['min_support']):
                catalog[r['rule_id']]=r
        sub=run/('support_'+support.replace('.','_'))
        sub.mkdir()
        jsonl_new(sub/'closed_input.jsonl',[{'items':sorted(k),'support_count':v} for k,v in closed.items()])
        jsonl_new(sub/'rules_filtered.jsonl',rules)
        csv_new(sub/'rules_filtered.csv',rules,RULE_FIELDS)
        exported={}
        for criterion in CRITERIA:
            top=top_rules(rules,criterion,args.top_k)
            csv_new(sub/f'top_rules_{criterion}.csv',top,RULE_FIELDS)
            exported[criterion]=len(top)
        summary.update({'min_support':support,'min_count':threshold.min_count(n),
                        'min_confidence':threshold.min_confidence,'min_lift':threshold.min_lift,
                        'n_closed':len(closed),'n_rules_exported':len(rules),
                        'top_k':args.top_k,'top_rows_exported':exported,
                        'source_dataset_hash':sha,'status':'complete_and_validated'})
        check={'min_support':support,'n_closed':len(closed),'n_recovered_frequent':len(frequent),
               'frequent_equals_verified_fp_or_tiny_oracle':True,'transaction_recount':recount,
               'independent_rule_comparison':comparison,'independent_counts':counts,
               'duplicate_rules':0,'all_rule_counts_come_from_recounted_itemsets':True,'passed':True}
        write_json(sub/'summary.json',summary)
        write_json(sub/'validation_report.json',check)
        summaries.append(summary)
        validation.append(check)
        provenance.append(origin)
        combined.extend(rules)
        print(json.dumps({'support':support,'frequent':len(frequent),'candidates':summary['n_candidates'],
                          'pass_confidence':summary['n_pass_confidence'],
                          'pass_lift_independent':summary['n_pass_lift_independent'],
                          'final':len(rules),'validation':'passed'},ensure_ascii=False),flush=True)
    del masks
    jsonl_new(run/'rules_filtered.jsonl',combined)
    unique=sorted(catalog.values(),key=lambda r:r['rule_id'])
    jsonl_new(run/'recommendation_rules.jsonl',unique)
    csv_new(run/'recommendation_rules.csv',unique,RULE_FIELDS)
    for criterion in CRITERIA:
        csv_new(run/f'top_rules_{criterion}.csv',top_rules(unique,criterion,args.top_k),RULE_FIELDS)
    write_json(run/'summary.json',summaries)
    fields=['min_support','min_count','n_closed','n_frequent','n_candidates','n_pass_confidence',
            'n_pass_lift_independent','n_pass_lift_after_confidence','n_final',
            'n_rejected_confidence','n_rejected_lift_after_confidence','n_rules_exported','status']
    csv_new(run/'summary.csv',summaries,fields)
    write_json(run/'input_provenance.json',provenance)
    write_json(run/'validation_report.json',{'unit_tests_passed':True,'randomized_configurations':360,
        'mlxtend_crosscheck':('available; optional crosscheck not invoked' if importlib.util.find_spec('mlxtend')
                             else 'not installed; no installation performed'),
        'thresholds':validation,'all_passed':True,'n_unique_recommendation_rules':len(unique),
        'combined_rows_include_threshold_context_duplicates':len(combined)-len(unique),
        'n_final_rows_across_thresholds':len(combined)})
    free_memory_guard(args.min_free_mib)
    charts(run,summaries,combined)
    report(run,args,summaries,unique,sha,n)
    after=protected_files(run)
    changed=[name for name,digest in before.items() if after.get(name)!=digest]
    write_json(run/'preservation_check.json',{'checked_files':len(before),'changed_or_missing':changed,
                                           'passed':not changed})
    if changed:
        raise RuntimeError('Pre-existing files changed during experiment')
    write_json(run/'completion.json',{'status':'completed','utc':utc_now(),
                                      'n_unique_rules':len(unique),'thresholds':len(summaries)})


def charts(run,summaries,rules):
    os.environ['MPLCONFIGDIR']=str(run/'matplotlib_config')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10})
    x=np.arange(len(summaries))
    fig,ax=plt.subplots(figsize=(9,5),layout='constrained')
    for offset,key,label,color in [(-.25,'n_candidates','Ứng viên','#9cacc0'),
            (0,'n_pass_confidence','Đạt confidence','#16828b'),(.25,'n_final','Đạt cả hai','#6753a5')]:
        bars=ax.bar(x+offset,[s[key] for s in summaries],width=.23,label=label,color=color)
        ax.bar_label(bars,fontsize=8,padding=2)
    ax.set_xticks(x,[f'{float(s["min_support"])*100:g}%' for s in summaries])
    ax.set_yscale('symlog',linthresh=10)
    ax.set_ylim(bottom=0,top=max(20,2*max(s['n_candidates'] for s in summaries)))
    ax.set_ylabel('Số luật (symlog, đoạn tuyến tính đến 10)')
    ax.set_xlabel('min_support')
    ax.set_title('Số luật trước và sau lọc — không áp dụng Top-K')
    ax.legend()
    for ext in ['png','svg']:
        fig.savefig(run/f'rule_counts.{ext}',dpi=160)
    plt.close(fig)
    fig,axes=plt.subplots(len(summaries),2,figsize=(10,2.4*len(summaries)),squeeze=False,layout='constrained')
    for i,s in enumerate(summaries):
        selected=[r for r in rules if r['min_support']==s['min_support']]
        for j,metric in enumerate(['confidence','lift']):
            ax=axes[i,j]
            values=[r[metric] for r in selected]
            if values:
                bins=np.linspace(0,1,11) if metric=='confidence' else 15
                ax.hist(values,bins=bins,color='#16828b' if j==0 else '#6753a5',edgecolor='white')
            else:
                ax.text(.5,.5,'Không có luật đạt ngưỡng',ha='center',transform=ax.transAxes)
            ax.set_title(f'Support {float(s["min_support"])*100:g}% — {metric}, n={len(selected)}')
            ax.set_ylabel('Số luật cuối cùng')
    for ext in ['png','svg']:
        fig.savefig(run/f'metric_distributions.{ext}',dpi=150)
    plt.close(fig)


def report(run,args,summaries,rules,sha,n):
    def percent(v): return f'{v*100:.2f}%'.replace('.',',')
    def sides(r): return '{'+', '.join(r['antecedents'])+'} → {'+', '.join(r['consequents'])+'}'
    lines=['# Giai đoạn 2 — Sinh luật kết hợp từ tập mục phổ biến đóng','',
        '## 1. Mục tiêu và dữ liệu','',
        'Sinh luật mua kèm, đo support/confidence/lift, lọc và xếp hạng; chuẩn bị dữ liệu cho website gợi ý. '
        'Chưa xây dựng website hoặc chạy lại benchmark.', '',
        f'- Số giao dịch: **{n:,}**; định danh sản phẩm: StockCode dạng chuỗi, giữ nguyên chữ hoa/thường và số 0 đầu.'.replace(f'{n:,}',f'{n:,}'.replace(',','.')),
        f'- SHA256 byte dataset: `{sha}`; hash chuẩn hóa LF và số sản phẩm nằm trong metadata.',
        '- Đầu vào ưu tiên là kết quả CHARM đã được kiểm chứng, đối chiếu lại tập đóng tham chiếu. '
        'Tập phổ biến FP-Growth được đọc từ benchmark đã hoàn thành; không khai thác lại.',
        '- Không có file Excel gốc hoặc bảng Description trong repository hiện tại. Không tải dữ liệu, '
        'không suy đoán tên/nhóm hàng; `product_names.json` rỗng để ghi nhận thiếu ánh xạ.', '',
        '## 2. Phương pháp và phạm vi đầy đủ','',
        'Với mỗi tập phổ biến X, closure(X) cũng phổ biến và có cùng support count. Vì vậy '
        '`count(X) = max(count(C) | C là tập đóng và X ⊆ C)`. Liệt kê các tập con không rỗng của '
        'toàn bộ tập đóng khôi phục mọi tập phổ biến và support của chúng, **nếu đầu vào tập đóng đầy đủ và đúng**.', '',
        'Với từng tập phổ biến U có ít nhất hai sản phẩm, liệt kê mọi X không rỗng, X ⊂ U, '
        'đặt Y=U\\X. Mỗi luật có đúng một union U và một vế trái X nên không bị lặp. '
        'Sinh **tất cả luật có union phổ biến và đạt các ngưỡng**, không phải tập luật cơ sở rút gọn. '
        'Không giới hạn số sản phẩm của hai vế. Số ứng viên = Σ(2^|U|−2).', '',
        'Phạm vi đầy đủ được hỗ trợ bằng lập luận trên, kiểm thử exhaustive nhỏ, đối chiếu toàn bộ '
        'tập phổ biến đã được kiểm chứng của FP-Growth, đếm lại support trên giao dịch và so sánh '
        'với bộ sinh luật độc lập. Điều này không chứng minh mọi đầu vào bên ngoài bất kỳ đều đầy đủ.', '',
        '## 3. Công thức, ngưỡng và xếp hạng','',
        '- support(X→Y) = count(X∪Y)/N.',
        '- confidence(X→Y) = count(X∪Y)/count(X).',
        '- lift(X→Y) = confidence(X→Y)/(count(Y)/N).',
        f'- Cấu hình: min_confidence={args.min_confidence}; min_lift={args.min_lift}; '
        f'min_support={", ".join(args.supports)}. Các ngưỡng được cấu hình qua CLI, lấy dấu ≥.',
        '- min_count làm tròn lên. So sánh ngưỡng và thứ tự xếp hạng dùng phân số/số nguyên chính xác; '
        'chỉ đổi float khi xuất chỉ số. Bộ kiểm chứng float dùng tolerance 1e−12; count so chính xác.',
        '- Confidence: giảm theo confidence, lift, support. Lift: giảm theo lift, confidence, support. '
        'Support: giảm theo support, confidence, lift.',
        '- Balanced score = support × confidence × max(0, 1−1/lift); giảm theo score, lift, confidence, support. '
        'Đây là heuristic trình bày có cân nhắc độ phổ biến và sức mạnh; không phải xác suất mua hay '
        'mô hình đã tối ưu bằng đánh giá gợi ý. Lift=1 cho score=0.',
        '- Nếu bằng mọi chỉ số, so sánh tuple StockCode của vế trái rồi vế phải theo từ điển tăng dần. '
        f'Top-{args.top_k} chỉ giới hạn bảng trình bày; toàn bộ luật cuối cùng vẫn được xuất.', '',
        '## 4. Kiểm thử và kiểm chứng','',
        '- Chạy unit test trước dữ liệu thật: bộ 6 giao dịch tính tay, empty/singleton/no-rule, '
        'hai sản phẩm luôn đi cùng, confidence=1, lift=1, ngưỡng biên, tie, nhiều sản phẩm mỗi vế, '
        'ngưỡng/count không hợp lệ và giới hạn tài nguyên.',
        '- Bộ độc lập: liệt kê toàn bộ tập trên vũ trụ nhỏ và quét giỏ; sinh luật bằng chia bitmask '
        '(không gọi generator cần kiểm chứng). 120 dataset ngẫu nhiên × 3 support = **360 cấu hình**.',
        '- Trên dữ liệu thật: đối chiếu closed CHARM với file tham chiếu; khôi phục frequent rồi so '
        'từng tập/count với FP-Growth. Đếm lại **mọi tập phổ biến** bằng giao bitset TID trên dataset gốc. '
        'Các count của hai vế và union vì vậy đều được kiểm tra.',
        '- Sau đó so **mọi luật cuối cùng**, ba support count, support/confidence/lift và số luật '
        'trước/sau lọc với generator bitmask độc lập từ frequent của FP-Growth.',
        '- mlxtend chưa có trong môi trường, không cài thêm. Kết quả kiểm thử/log lưu trong '
        '`unit_tests.json`, `unit_test.log`, `validation_report.json`.', '',
        '## 5. Kết quả theo ngưỡng','',
        '“Đạt lift độc lập” xét trên mọi ứng viên; “cuối cùng” là đạt cả confidence và lift. '
        'Số đạt lift sau lọc confidence bằng số cuối cùng.', '',
        '| Support | min_count | Closed/Frequent | Ứng viên | Đạt confidence | Đạt lift độc lập | Cuối cùng | Xuất toàn bộ |',
        '|---:|---:|---:|---:|---:|---:|---:|---:|']
    for s in summaries:
        lines.append(f'| {float(s["min_support"])*100:g}% | {s["min_count"]} | {s["n_closed"]}/{s["n_frequent"]} | '
                     f'{s["n_candidates"]} | {s["n_pass_confidence"]} | {s["n_pass_lift_independent"]} | '
                     f'{s["n_final"]} | {s["n_rules_exported"]} |')
    lines += ['', 'Ở support 5%, đầu ra hiện có chỉ gồm 28 singleton nên không có luật hai vế không rỗng; '
              'đây là lý do không chọn ngưỡng đó trong thực nghiệm mặc định.', '',
              '![Số luật và bộ lọc](rule_counts.png)', '',
              '### Phân bố chỉ số và kích thước luật', '',
              '| Support | Confidence min/median/p95/max | Lift min/median/p95/max | Kích thước X→Y (số luật cuối cùng) |',
              '|---:|---|---|---|']
    for s in summaries:
        cells=[]
        for metric in ['final_confidence','final_lift']:
            d=s[metric]
            cells.append('/'.join('—' if d[k] is None else f'{d[k]:.4f}' for k in ['min','median','p95','max']))
        lines.append(f'| {float(s["min_support"])*100:g}% | {cells[0]} | {cells[1]} | {json.dumps(s["final_shapes"])} |')
    lines += ['', 'Mean, sample SD, p25/p75/p95, min/max của cả ứng viên và luật cuối cùng nằm trong `summary.json`. '
              'Percentile dùng nội suy tuyến tính tại vị trí (n−1)p, SD bỏ trống nếu n<2.', '',
              '![Phân bố confidence và lift](metric_distributions.png)', '',
              '## 6. Luật tiêu biểu và ý nghĩa mua kèm','',
              'Không có tên sản phẩm đáng tin cậy nên chỉ mô tả đồng xuất hiện theo StockCode. '
              'Các luật sau chọn theo balanced score, ưu tiên union khác nhau để tránh chỉ lặp luật đảo chiều.', '',
              '| Luật | Count union | Count X / Count Y | Support | Confidence | Lift |',
              '|---|---:|---:|---:|---:|---:|']
    selected=[]
    seen=set()
    for r in top_rules(rules,'balanced',max(1,len(rules))):
        union=tuple(sorted(r['antecedents']+r['consequents']))
        if union not in seen:
            selected.append(r)
            seen.add(union)
        if len(selected)>=5: break
    for r in selected:
        lines.append(f'| {sides(r)} | {r["support_count"]} | {r["antecedent_support_count"]} / '
                     f'{r["consequent_support_count"]} | {percent(r["support"])} | '
                     f'{percent(r["confidence"])} | {r["lift"]:.3f} |')
    lines.append('')
    for r in selected:
        lines.append(f'- **{sides(r)}**: {r["support_count"]} hóa đơn có cả hai vế; '
                     f'trong {r["antecedent_support_count"]} hóa đơn chứa vế trái, '
                     f'{percent(r["confidence"])} cũng chứa đầy đủ vế phải. '
                     f'Lift {r["lift"]:.3f} so với tần suất vế phải trên toàn dữ liệu; '
                     'đây là liên hệ đồng giỏ trong mẫu, không chứng minh tác động của việc gợi ý.')
    if rules:
        examples={c:top_rules(rules,c,1)[0] for c in ['support','confidence','lift']}
        lines += ['', '### Phổ biến, tin cậy và lift cao là ba góc nhìn khác nhau', '']
        for criterion,r in examples.items():
            lines.append(f'- Đứng đầu **{criterion}**: {sides(r)}; support={percent(r["support"])}, '
                         f'confidence={percent(r["confidence"])}, lift={r["lift"]:.3f}, '
                         f'count={r["support_count"]}.')
        lines.append('')
        lines.append('Support cao cho biết phạm vi tiếp cận theo hóa đơn; confidence cao là xác suất có điều kiện '
                     'trong mẫu; lift cao là độ lệch so với mức phổ biến nền. Không ưu tiên chỉ dựa vào lift: '
                     'cần đọc cùng số hóa đơn, support và quy mô tập điều kiện. Các ngưỡng khác nhau tạo ra '
                     'các tập luật lồng nhau; số luật ở nhiều ngưỡng không được cộng thành số quan hệ duy nhất.')
        multi=[r for r in rules if len(r['antecedents'])>1 or len(r['consequents'])>1]
        if multi:
            r=top_rules(multi,'balanced',1)[0]
            lines += ['', f'Ví dụ luật nhiều sản phẩm: **{sides(r)}**, support={percent(r["support"])}, '
                      f'confidence={percent(r["confidence"])}, lift={r["lift"]:.3f}. '
                      'Không được tách một consequent nhiều sản phẩm thành các luật riêng rồi giữ nguyên chỉ số.']
    lines += ['', '## 7. Schema và dữ liệu cho website gợi ý','',
        f'- `recommendation_rules.jsonl`: **{len(rules)} luật duy nhất** qua các ngưỡng, chọn bản ghi có '
        'min_support nhỏ nhất khi cùng rule_id. Dùng file này làm catalog, không cộng các dòng lặp theo ngưỡng.',
        '- `rule_schema.json` mô tả schema v1.0; mỗi dòng có rule_id, hai vế, ba support count, N, '
        'support/confidence/lift, balanced score, các ngưỡng và source_dataset_hash. '
        'Ngưỡng lưu dạng chuỗi thập phân; chỉ số lưu số thực. CSV lưu hai vế bằng JSON array.',
        '- rule_id = SHA256(dataset hash + hai vế đã chuẩn hóa), giữ ổn định qua các ngưỡng trên cùng dataset. '
        'Rule ID khác nhau khi đổi hướng luật; mã sản phẩm không bị đổi sang số hoặc tên.',
        '- Website có thể kích hoạt luật khi toàn bộ antecedent thuộc giỏ hiện tại. Consequent là một tập '
        'sản phẩm được đồng quan sát; nếu loại các món đã có khỏi consequent thì phải ghi rõ đó là thao tác '
        'giao diện, không tái sử dụng các chỉ số như thể luật đã được tính lại.',
        '- Cần hợp nhất các gợi ý trùng sản phẩm, xử lý giỏ không khớp luật, tồn kho, thời gian và ánh xạ '
        'tên đã xác minh ở giai đoạn sau. Chưa đánh giá precision/recall/hit-rate hay hiệu quả kinh doanh.', '',
        '## 8. Giới hạn, tài nguyên và an toàn','',
        '- Luật kết hợp không phải quan hệ nhân quả; không khẳng định khách chắc chắn sẽ mua Y sau X. '
        'Confidence=1 chỉ mô tả mẫu quan sát với số hóa đơn hữu hạn.',
        '- Hóa đơn là đơn vị phân tích, không phải khách hàng độc lập. Chưa có phân nhóm người mua, '
        'mùa vụ, giá trị đơn, số lượng mua, giá bán hoặc diễn biến thời gian trong file giao dịch hiện tại.',
        '- Chưa tách train/test theo thời gian; chưa đánh giá luật ngoài mẫu, thống kê nhiều giả thuyết, '
        'bootstrap hay độ bền theo mùa. Lift cao có thể đi cùng support thấp và điều kiện hiếm.',
        '- Ở dải ngưỡng này closed và frequent có số lượng bằng nhau: chưa thể khẳng định tiết kiệm '
        'lưu trữ nhờ tập đóng trên dữ liệu thật. Kiểm thử nhỏ có trường hợp nén và vẫn khôi phục đúng.',
        '- Không có mô tả sản phẩm nên chưa gán nhãn danh mục hoặc đưa ra đề xuất merchandising theo tên. '
        'Không tải nguồn ngoài hoặc bịa thông tin sản phẩm.',
        f'- Giới hạn công khai: {args.max_candidates} ứng viên/ngưỡng, {args.max_frequent} tập phổ biến, '
        f'{args.max_subset_visits} lượt tập con; dừng nếu RAM khả dụng dưới {args.min_free_mib} MiB. '
        'Vượt giới hạn sẽ báo lỗi và giữ dấu vết, không cắt bớt rồi báo đầy đủ.',
        '- Toàn bộ benchmark và dữ liệu cũ được kiểm tra hash trước/sau. Không chỉnh thuật toán, '
        'không cài thư viện hoặc commit/push.', '',
        '## 9. Tệp kết quả và cách chạy lại','',
        '- `metadata.json`, `input_provenance.json`: cấu hình, hash, nguồn CHARM/FP-Growth và môi trường.',
        '- `summary.csv`/`summary.json`, `validation_report.json`, `unit_test.log`: số đếm và kiểm chứng.',
        '- `rules_filtered.jsonl`: toàn bộ luật cuối cùng theo từng ngưỡng; cùng rule_id có thể có nhiều '
        'dòng vì khác ngữ cảnh min_support. Khóa dòng là (rule_id, min_support, min_confidence, min_lift).',
        '- `support_*/`: closed_input, toàn bộ luật CSV/JSONL, Top-K theo bốn tiêu chí và kiểm chứng từng ngưỡng.',
        '- `top_rules_confidence.csv`, `top_rules_lift.csv`, `top_rules_support.csv`, `top_rules_balanced.csv`: '
        'Top-K toàn catalog đã bỏ trùng; số dòng được xuất khác tổng số luật hợp lệ.',
        '- `rule_schema.json`, `recommendation_rules.jsonl`/`.csv`, `product_names.json`: giao diện dữ liệu v1.0.', '',
        '```powershell',
        'python -B src/validation/run_association_rules.py --smoke-test',
        'python -B src/validation/run_association_rules.py --supports 0.03 0.02 0.01 0.005 --min-confidence 0.5 --min-lift 1.0 --top-k 20',
        '```', '',
        'Mỗi lệnh tạo thư mục mới có UTC timestamp và UUID; xuất bằng chế độ exclusive, không ghi đè.', '',
        '## 10. Kết luận và bước tiếp theo','',
        'Đã xây dựng và kiểm chứng pipeline sinh đầy đủ luật đạt ngưỡng từ tập đóng cho các đầu vào hiện có, '
        'bao gồm luật nhiều sản phẩm ở cả hai vế. Báo cáo phân biệt số ứng viên, số qua từng bộ lọc, '
        'luật cuối cùng và Top-K trình bày. Kết quả là cơ sở phân tích đồng mua và dữ liệu đầu vào cho '
        'demo, chưa phải bằng chứng hiệu quả gợi ý ngoài thực tế.', '',
        'Giai đoạn tiếp theo có thể xây website đọc catalog và ánh xạ tên đã xác minh, rồi đánh giá '
        'theo thời gian trên tập kiểm tra và so với baseline phổ biến. Chưa triển khai những phần đó ở đây.', '']
    if args.smoke_test:
        lines.insert(2,'**SMOKE TEST: chỉ dùng 6 giao dịch tổng hợp và đáp án liệt kê độc lập; không phải kết quả dữ liệu thật.**')
    with (run/'report.md').open('x',encoding='utf-8') as f:
        f.write('\n'.join(lines))


def main():
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--data',type=Path,default=ROOT/'data/processed/transactions_v1.jsonl')
    parser.add_argument('--benchmarks-root',type=Path,default=ROOT/'results/benchmarks')
    parser.add_argument('--output-root',type=Path,default=ROOT/'results/association_rules')
    parser.add_argument('--supports',nargs='+')
    parser.add_argument('--min-confidence',default='0.5')
    parser.add_argument('--min-lift',default='1.0')
    parser.add_argument('--top-k',type=int,default=20)
    parser.add_argument('--max-candidates',type=int,default=250000)
    parser.add_argument('--max-frequent',type=int,default=100000)
    parser.add_argument('--max-subset-visits',type=int,default=500000)
    parser.add_argument('--min-free-mib',type=int,default=128)
    parser.add_argument('--smoke-test',action='store_true')
    args=parser.parse_args()
    try:
        supports=args.supports or (['0.3','0.5'] if args.smoke_test else ['0.03','0.02','0.01','0.005'])
        ts=[Thresholds(s,args.min_confidence,args.min_lift) for s in supports]
        args.supports=[t.min_support for t in ts]
        if len(set(args.supports))!=len(args.supports):
            raise ValueError('Duplicate supports')
        args.supports.sort(key=Decimal,reverse=True)
        args.min_confidence,args.min_lift=ts[0].min_confidence,ts[0].min_lift
        if min(args.top_k,args.max_candidates,args.max_frequent,args.max_subset_visits,args.min_free_mib)<1:
            raise ValueError('Top-K and resource limits must be positive')
    except ValueError as exc:
        parser.error(str(exc))
    args.output_root=args.output_root.resolve()
    args.output_root.mkdir(parents=True,exist_ok=True)
    run=args.output_root/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:12])
    run.mkdir()
    print('Run:',run,flush=True)
    try:
        execute(args,run)
    except (Exception,KeyboardInterrupt) as exc:
        write_json(run/'error.json',{'type':type(exc).__name__,'error':str(exc),'utc':utc_now(),
                                    'status':'incomplete; no completeness claim'})
        print(f'ERROR: {type(exc).__name__}: {exc}',file=sys.stderr)
        return 1
    print('Report:',run/'report.md',flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
