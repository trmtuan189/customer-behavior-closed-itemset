"""Phase 4 offline evaluation. Each invocation creates a new, exclusive run."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
import traceback
import uuid
import csv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'src' / 'validation'))
from recommendation.engine import RuleEngine, load_catalog, read_jsonl, popular_recommend
from recommendation.evaluation import temporal_split, mask_basket, metrics
import psutil

PHASE3 = ROOT / 'results/customer_analysis/20261028T162806Z_a0bcc2798234'
DATA = ROOT / 'data/processed/transactions_v1.jsonl'


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def dump(path, value):
    with open(path, 'x', encoding='utf-8', newline='\n') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)


def jsonl(path, rows):
    with open(path, 'x', encoding='utf-8', newline='\n') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True, allow_nan=False)+'\n')


def write_csv(path, rows):
    rows = list(rows)
    if not rows:
        raise ValueError('CSV cannot be empty')
    with open(path, 'x', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def source_manifest():
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted((ROOT/'src').rglob('*.py'))}


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True, encoding='utf-8').strip()


def worker(train_path, out, config):
    from charm_impl import charm_mine
    from reference_miner import mine_frequent
    from rules.association_rules import Thresholds, recover_frequent, generate_rules
    from association_rule_reference import reference_rules, compare_rules
    baskets = [r['items'] for r in read_jsonl(train_path)]
    thresholds = Thresholds(config['min_support'], config['min_confidence'], config['min_lift'])
    started = time.perf_counter()
    closed_rows, stats = charm_mine(baskets, thresholds.min_count(len(baskets)),
                                   timeout_seconds=config['timeout_seconds'])
    mining_seconds = time.perf_counter()-started
    closed = {frozenset(r['items']): r['support_count'] for r in closed_rows}
    recovered = recover_frequent(closed, len(baskets), thresholds)
    ref = mine_frequent(baskets, thresholds.min_count(len(baskets)))
    if recovered != ref:
        raise AssertionError('CHARM recovered frequent family != independent Eclat')
    # Downward completeness + immediate supersets is sufficient for closure check.
    nonclosed = set()
    for x, count in ref.items():
        if len(x) > 1:
            for item in x:
                if ref[x-{item}] == count:
                    nonclosed.add(x-{item})
    if closed != {x: c for x, c in ref.items() if x not in nonclosed}:
        raise AssertionError('Closed family mismatch')
    train_hash = sha(train_path)
    rules, summary = generate_rules(recovered, len(baskets), thresholds, train_hash)
    expected, _ = reference_rules(ref, len(baskets), thresholds.min_confidence, thresholds.min_lift)
    validation = compare_rules(rules, expected)
    jsonl(out/'rules.jsonl', sorted(rules, key=lambda r: r['rule_id']))
    jsonl(out/'closed.jsonl', sorted(closed_rows, key=lambda r: r['items']))
    dump(out/'worker_result.json', {'status': 'success', 'mining_seconds': mining_seconds,
         'pipeline_seconds': time.perf_counter()-started, 'n_closed': len(closed),
         'n_frequent': len(ref), 'n_rules': len(rules), 'rule_validation': validation,
         'min_count': thresholds.min_count(len(baskets)), 'rule_summary': summary,
         'train_sha256': train_hash, 'stats': stats})


def run_worker(run, train_path, config, label):
    out = run/label
    out.mkdir()
    cmd = [sys.executable, '-B', str(Path(__file__).resolve()), '--worker', str(train_path),
           '--worker-out', str(out), '--config-path', str(run/'evaluation_config.json')]
    started = time.perf_counter()
    peak = 0
    low_since = None
    status = 'success'
    with open(out/'stdout.log', 'x', encoding='utf-8') as stdout, open(out/'stderr.log', 'x', encoding='utf-8') as stderr:
        proc = subprocess.Popen(cmd, cwd=ROOT, stdout=stdout, stderr=stderr,
                                env={**os.environ, 'PYTHONHASHSEED': '0', 'PYTHONDONTWRITEBYTECODE': '1'})
        child = psutil.Process(proc.pid)
        while proc.poll() is None:
            elapsed = time.perf_counter()-started
            try:
                peak = max(peak, child.memory_info().rss)
            except psutil.NoSuchProcess:
                pass
            available = psutil.virtual_memory().available
            low_since = (low_since or time.monotonic()) if available < 128*1024**2 else None
            if elapsed > config['timeout_seconds']:
                status = 'timeout'
            if low_since and time.monotonic()-low_since > 5:
                status = 'low_memory_stop'
            if status != 'success':
                proc.kill()
                break
            time.sleep(.05)
        proc.wait()
    if status == 'success' and proc.returncode:
        status = 'error'
    result = {'status': status, 'returncode': proc.returncode,
              'wall_seconds': time.perf_counter()-started,
              'sampled_peak_rss_mib': peak/1024**2, 'rss_interval_seconds': .05,
              'rss_scope': 'whole training worker including imports, validation and exports'}
    dump(out/'process_result.json', result)
    if status != 'success':
        raise RuntimeError(f'{label}: {status}; xem {out}/stderr.log')
    return out


def evaluate(cases, rules, catalog, counts, config, run):
    engine = RuleEngine(catalog, rules)
    all_rows = []
    union = {(m, k): set() for m in ('popular', 'association_rules') for k in config['top_k']}
    with open(run/'evaluation_results.csv', 'x', encoding='utf-8-sig', newline='') as f:
        fields = ['case_id', 'method', 'k', 'target_count', 'predicted', 'targets',
                  'precision', 'recall', 'hit_rate', 'ndcg', 'received']
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for case in cases:
            basket, target = set(case['input']), set(case['targets'])
            for method in ('popular', 'association_rules'):
                if method == 'popular':
                    pred = popular_recommend(counts, basket, max(config['top_k']))
                else:
                    pred = [r['stock_code'] for r in engine.recommend(basket, max(config['top_k']),
                            float(config['min_confidence']), float(config['min_lift']))]
                assert not set(pred) & basket and len(pred) == len(set(pred))
                for k in config['top_k']:
                    top = pred[:k]
                    row = {'case_id': case['case_id'], 'method': method, 'k': k,
                           'target_count': len(target), 'predicted': json.dumps(top),
                           'targets': json.dumps(sorted(target)), **metrics(top, target, k)}
                    w.writerow(row)
                    all_rows.append(row)
                    union[method, k].update(top)
            f.flush()  # persist each completed case
    summary = []
    for (method, k), recommended in union.items():
        rows = [r for r in all_rows if r['method'] == method and r['k'] == k]
        n = len(rows)
        summary.append({'method': method, 'k': k, 'evaluation_baskets': n,
            **{name: sum(r[name] for r in rows)/n for name in ('precision', 'recall', 'hit_rate', 'ndcg', 'received')},
            'no_recommendation_rate': 1-sum(r['received'] for r in rows)/n,
            'catalog_coverage': len(recommended)/len(counts),
            'recommended_distinct_products': len(recommended), 'training_catalog_size': len(counts)})
    write_csv(run/'metrics_summary.csv', summary)
    write_csv(run/'baseline_comparison.csv', summary)
    return summary


def independent_metric_check(run, cases, counts):
    """Recompute from serialized lists; deliberately does not call metrics()."""
    import math
    groups = {}
    case_map = {c['case_id']: c for c in cases}
    checked = 0
    for r in csv.DictReader(open(run/'evaluation_results.csv', encoding='utf-8-sig')):
        case = case_map[int(r['case_id'])]
        p, t, k = json.loads(r['predicted']), set(json.loads(r['targets'])), int(r['k'])
        assert t == set(case['targets']) and not set(p)&set(case['input'])
        assert len(p) == len(set(p)) and len(p) <= k and set(p) <= counts.keys()
        hits = set(p)&t
        precision, recall, hit = len(hits)/k, len(hits)/len(t), float(bool(hits))
        gain = sum(1/math.log2(rank+1) for rank, item in enumerate(p, 1) if item in t)
        ideal = sum(1/math.log2(rank+1) for rank in range(1, min(k, len(t))+1))
        vals = dict(precision=precision, recall=recall, hit_rate=hit, ndcg=gain/ideal, received=int(bool(p)))
        for name, value in vals.items():
            assert math.isclose(value, float(r[name]), rel_tol=1e-12, abs_tol=1e-12)
        key = (r['method'], k)
        g = groups.setdefault(key, {'rows': [], 'products': set()})
        g['rows'].append(vals)
        g['products'].update(p)
        checked += 1
    for r in csv.DictReader(open(run/'metrics_summary.csv', encoding='utf-8-sig')):
        g = groups[r['method'], int(r['k'])]
        assert len(g['rows']) == len(cases)
        for name in ('precision', 'recall', 'hit_rate', 'ndcg', 'received'):
            assert math.isclose(sum(x[name] for x in g['rows'])/len(cases), float(r[name]), abs_tol=1e-12)
        assert math.isclose(len(g['products'])/len(counts), float(r['catalog_coverage']), abs_tol=1e-12)
    return {'status': 'passed', 'serialized_metric_rows_recomputed': checked,
            'cases': len(cases), 'empty_recommendations_in_denominator': True}


def charts_and_report(run, metadata, summary, example):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    (run/'charts').mkdir()
    chart_manifest = []
    for metric, label in [('precision', 'Precision@K'), ('recall', 'Recall@K'),
                          ('hit_rate', 'Hit Rate@K'), ('catalog_coverage', 'Catalog coverage'),
                          ('received', 'Tỷ lệ giỏ có gợi ý')]:
        fig, ax = plt.subplots(figsize=(7.5, 4.3))
        for method, color in [('popular', '#64748b'), ('association_rules', '#0d9488')]:
            rows = [r for r in summary if r['method'] == method]
            offset = -.16 if method == 'popular' else .16
            ax.bar([i+offset for i in range(len(rows))], [r[metric] for r in rows],
                   width=.3, label=method, color=color)
        ax.set_xticks([0, 1], ['Top-5', 'Top-10'])
        ax.set_ylabel(label)
        ax.set_ylim(bottom=0)
        ax.legend(fontsize=8)
        ax.grid(axis='y', alpha=.2)
        fig.tight_layout()
        fig.savefig(run/f'charts/{metric}.png', dpi=180)
        plt.close(fig)
        write_csv(run/f'charts/{metric}.csv', [{'method': r['method'], 'k': r['k'], metric: r[metric]} for r in summary])
        chart_manifest.append({'png': f'{metric}.png', 'csv': f'{metric}.csv'})
    dump(run/'charts/manifest.json', chart_manifest)
    split, cfg = metadata['split'], metadata['config']
    lines = ['# Giai đoạn 4 — Gợi ý sản phẩm và đánh giá ngoài mẫu',
      '', 'Ngày bàn giao: 30/10/2026, Asia/Saigon. Timestamp thực thi giữ nguyên UTC trong metadata.', '',
      '## 1. Mục tiêu và kiến trúc', '',
      'CHARM khai thác tập đóng; module rules khôi phục tập phổ biến và sinh luật đầy đủ; RuleEngine chọn ứng viên và xếp hạng; evaluator đo khả năng hoàn thiện giỏ. Bốn công việc này độc lập về ý nghĩa. Tốc độ khai thác không chứng minh chất lượng gợi ý.', '',
      '## 2. Dữ liệu và phép chia', '',
      f"Dataset SHA256: `{metadata['dataset_sha256']}`. Giai đoạn 3: 39.529 giỏ / 4.904 SKU, 830 hóa đơn bản sao đã được chứng minh và chọn một bản; không thay quy tắc làm sạch.",
      f"Training **{split['train']}**, test **{split['test']}**, ngày ranh giới **{split['boundary_day']}**. Mọi hóa đơn trong ngày ranh giới vào test; training chỉ có ngày trước đó. Ngày cuối training: {split['train_max_date']}; ngày đầu test: {split['test_min_date']}. Loại do ngày thiếu/chưa xác minh: {split['excluded_missing_or_unverified_date']}.",
      f"Giỏ test đủ điều kiện **{metadata['evaluation_baskets']}**; loại giỏ <2 món: {metadata['test_singletons_excluded']}. Không chia dòng hóa đơn. Chia xấp xỉ 80/20 theo số hóa đơn; không ép đúng tỷ lệ bằng cách cắt ngày.", '',
      '## 3. Huấn luyện và chống leakage', '',
      f"CHARM chỉ nhận train.jsonl (chỉ danh sách SKU, không ID/ngày/khách); support={cfg['min_support']}, confidence={cfg['min_confidence']}, lift={cfg['min_lift']}; timeout toàn worker {cfg['timeout_seconds']} giây. Các ngưỡng này ấn định trước khi đo test, lấy cấu hình 0,5% đã kiểm chứng ở giai đoạn trước. Không khảo sát hay chọn ngưỡng theo test.",
      f"Training: {metadata['training']['n_frequent']} tập phổ biến, {metadata['training']['n_closed']} tập đóng, {metadata['training']['n_rules']} luật. So sánh chính xác với Eclat độc lập và bộ sinh luật tham chiếu. Chạy lại mining độc lập cho cùng hash luật và tập đóng: {metadata['reproducible']}.",
      'Most Popular chỉ đếm số giỏ chứa SKU trong training. Luật toàn dataset 5.173 luật chỉ phục vụ demo mô tả, tuyệt đối không làm đầu vào evaluator. Catalog toàn kỳ chỉ thêm tên sau khi đã chọn mã; tên, độ tin cậy và tần suất catalog không tham gia ranking, baseline hay masking.', '',
      '## 4. Tình huống và metric', '',
      f"Mỗi giỏ test >=2 SKU: sắp xếp mã, xáo bằng seed riêng SHA256(seed:case_id), seed gốc {cfg['seed']}; lấy floor(n/2) mã làm đầu vào, phần còn lại làm mục tiêu. Một mask/giỏ; hai phương pháp dùng đúng cùng mask. Đây là hoàn thiện giỏ đồng mua, không dự báo thứ tự mua hay lần mua sau. File cases nằm internal, không cấp cho web.",
      'N là toàn bộ giỏ đủ điều kiện, kể cả không có gợi ý. Precision@K=|TopK∩target|/K (thiếu K vẫn chia K). Recall@K=hits/|target|. Hit Rate@K=I(hits>0). Báo cáo trung bình macro trên N. NDCG@K=Σ rel_i/log2(i+1) chia ideal DCG với min(K,|target|) hit. Giỏ không có gợi ý nhận 0 ở mọi metric.',
      'Catalog coverage@K=|hợp SKU được gợi ý trên N giỏ|/|SKU trong training|. Tỷ lệ giỏ có gợi ý= số giỏ có ít nhất 1 kết quả/N, khác catalog coverage. Mẫu số training catalog giúp không tính SKU chỉ xuất hiện ở tương lai. Kèm số đếm tuyệt đối trong CSV.', '',
      '## 5. Kết quả thực đo', '',
      '| Phương pháp | K | Precision | Recall | Hit Rate | NDCG | Catalog coverage | Giỏ có gợi ý |',
      '|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in summary:
        lines.append('| '+r['method']+' | '+str(r['k'])+' | '+' | '.join(f"{r[x]:.6f}" for x in ('precision','recall','hit_rate','ndcg','catalog_coverage','received'))+' |')
    for metric in ('precision','recall','hit_rate','catalog_coverage','received'):
        lines += ['', f'![{metric}](charts/{metric}.png)']
    for k in cfg['top_k']:
        a = next(r for r in summary if r['method']=='association_rules' and r['k']==k)
        b = next(r for r in summary if r['method']=='popular' and r['k']==k)
        lines += ['', f"K={k}: chênh lệch luật − phổ biến: Precision {a['precision']-b['precision']:+.6f}, Recall {a['recall']-b['recall']:+.6f}, Hit Rate {a['hit_rate']-b['hit_rate']:+.6f}. Đây là chênh lệch mô tả của một lần chia, chưa kiểm định ý nghĩa thống kê."]
    lines += ['', '## 6. Engine và ví dụ', '',
      'Chỉ kích hoạt khi toàn bộ antecedent nằm trong giỏ. Mỗi SKU chưa có trong consequent là một ứng viên. Điểm luật = support × confidence × max(0,1−1/lift), tính Fraction từ count, không làm tròn trước ranking. Nếu nhiều luật trỏ cùng SKU, lấy MAX, tránh cộng các bằng chứng tương quan hoặc luật dư thừa. Điểm cùng nhau lần lượt so confidence, lift, support giảm dần, rồi hai vế luật/rule_id/StockCode tăng dần. Luật nhiều consequent truyền cùng điểm joint cho từng SKU; confidence là xác suất đồng consequent theo luật, không phải xác suất biên cá nhân đã hiệu chuẩn.',
      'Không đề xuất SKU trong giỏ, không trùng mã. Unknown SKU trả thông báo lỗi, không có luật trả danh sách rỗng. SKU thiếu tên đáng tin hiển thị chính mã. Ngưỡng tùy chọn chỉ lọc lại luật đã có; hạ dưới ngưỡng mining không khôi phục được luật bị loại.',
      'Ví dụ demo mô tả dùng luật toàn kỳ, giỏ [22386]; không phải ví dụ đánh giá độc lập:']
    for r in example:
        lines.append(f"- {r['stock_code']} — {r['description']}: support={r['support']:.6f}, confidence={r['confidence']:.6f}, lift={r['lift']:.6f}, score={r['score']:.8f}; {r['rule']['antecedents']} → {r['rule']['consequents']}.")
    lines += ['', '## 7. Demo và bảo mật', '',
      'Demo local dùng thư viện chuẩn Python vì Streamlit chưa được cài. Bốn trang: Tổng quan, Gợi ý sản phẩm, Phân tích dữ liệu, Đánh giá hệ thống. Chỉ bind 127.0.0.1. Không đăng nhập, dịch vụ ngoài hoặc upload. Backend chỉ đọc allowlist catalog/luật đã công khai, ảnh tổng hợp có CSV nguồn và metrics_summary. Không nạp transaction_links, cases, customer-level hoặc khóa HMAC vào web. Hai chế độ rõ ràng: luật toàn kỳ (mô tả) và luật training (đã đánh giá).',
      f"Chạy: `python -B src/demo/app.py --evaluation {run.relative_to(ROOT).as_posix()}`. Mở http://127.0.0.1:8765. Xem hướng dẫn DEMO_GUIDE_VI.md. Kiểm thử HTTP và engine tự động nằm test_report.json; không tuyên bố đã kiểm thử giao diện thủ công nếu chưa thực hiện.", '',
      '## 8. Tái lập và kiểm chứng', '',
      'metadata.json lưu Git commit/status/diff, hash source, phiên bản, SHA đầu vào, timeout và sampled peak RSS toàn worker (50 ms), không gọi là RAM riêng thuật toán. Hai worker chạy tuần tự. audit/ lưu kiểm tra leakage, preservation và tính lại metrics độc lập từ CSV. Test_report.json ghi số test, kiểm thử HTTP và giới hạn QA.', '',
      '## 9. Hạn chế và hướng phát triển', '',
      'Một temporal split, một mask/giỏ, chưa có cross-validation theo thời gian, confidence interval, kiểm định thống kê hoặc A/B test. Random mask bỏ qua thứ tự thêm giỏ, giá, tồn kho và thời điểm ngừng bán; dữ liệu chỉ đến 2011. SKU test chưa từng xuất hiện training không thể được dự đoán nhưng vẫn nằm mẫu số Recall; số lượng ghi metadata. Catalog tên toàn kỳ chỉ phục vụ trình bày, không chứng minh metadata tại thời điểm triển khai. Giỏ lớn có trọng số macro như giỏ nhỏ; số mục tiêu lớn làm Recall@K thấp. Chưa có recommendation cá nhân theo khách.',
      'Tập đóng giữ thông tin support để sinh luật mà có thể giảm tập dư thừa trên dữ liệu phù hợp. Tỷ lệ nén thực tế ghi metadata; nếu gần 1 thì không khẳng định giảm đầu ra. Không so chất lượng ba miner bằng cùng luật và gán khác biệt cho CHARM. Đánh giá chỉ so công cụ chọn gợi ý với phổ biến.',
      'Giai đoạn 5: đưa phương pháp và bảng/đồ thị đã kiểm chứng vào khóa luận, phân biệt benchmark và recommendation; thêm rolling temporal splits/độ bất định nếu cần nghiên cứu tiếp; chuẩn bị slide, kịch bản demo có/không luật, SKU thiếu tên và câu hỏi về leakage/privacy. Không hứa tăng doanh thu từ phép đo offline.', '',
      '## 10. Hồ sơ', '',
      'metadata.json; evaluation_config.json; evaluation_results.csv; metrics_summary.csv; baseline_comparison.csv; training_first/ và training_repeat/; charts/ kèm CSV; audit/; test_report.json. internal/ chứa đầu vào chia/mask, không công khai. Kết quả giai đoạn cũ không ghi đè.']
    with open(run/'report.md', 'x', encoding='utf-8') as f:
        f.write('\n'.join(lines)+'\n')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--phase3', type=Path, default=PHASE3)
    p.add_argument('--support', default='0.005')
    p.add_argument('--confidence', default='0.5')
    p.add_argument('--lift', default='1')
    p.add_argument('--seed', type=int, default=20261030)
    p.add_argument('--timeout', type=int, default=180)
    p.add_argument('--worker', type=Path)
    p.add_argument('--worker-out', type=Path)
    p.add_argument('--config-path', type=Path)
    args = p.parse_args()
    if args.worker:
        worker(args.worker, args.worker_out, json.loads(args.config_path.read_text(encoding='utf-8')))
        return
    from rules.association_rules import Thresholds
    Thresholds(args.support, args.confidence, args.lift)
    if args.timeout <= 0:
        p.error('timeout must be positive')
    started = time.perf_counter()
    # Snapshot every old data/result/source/test file BEFORE creating this run.
    before = {str(f.relative_to(ROOT)): sha(f) for folder in ('data', 'results', 'src', 'tests')
              for f in (ROOT/folder).rglob('*') if f.is_file() and '__pycache__' not in f.parts}
    run = ROOT/'results/recommendation_evaluation'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:12])
    run.mkdir(parents=True, exist_ok=False)
    (run/'internal').mkdir()
    (run/'audit').mkdir()
    (run/'.gitignore').write_text('internal/\n', encoding='utf-8')
    config = {'train_ratio_target': .8, 'split_unit': 'calendar day / whole invoice',
        'seed': args.seed, 'min_support': args.support, 'min_confidence': args.confidence,
        'min_lift': args.lift, 'top_k': [5, 10], 'mask': 'seeded shuffled half known, ceil half hidden',
        'algorithm': 'existing CHARM + closed recovery + existing complete rule generator',
        'timeout_seconds': args.timeout, 'threshold_selection': 'fixed a priori; no test tuning'}
    dump(run/'evaluation_config.json', config)
    metadata = {'status': 'running', 'created_utc': datetime.now(timezone.utc).isoformat(),
        'phase3': str(args.phase3.resolve()), 'dataset_sha256': sha(DATA), 'config': config,
        'git_commit': git('rev-parse','HEAD'), 'git_status': git('status','--short'),
        'git_diff': git('diff'), 'source_sha256': source_manifest(),
        'environment': {'python': sys.version, 'platform': platform.platform(),
             'cpu': platform.processor(), 'logical_cpus': psutil.cpu_count(),
             'physical_cpus': psutil.cpu_count(logical=False), 'ram_bytes': psutil.virtual_memory().total,
             'available_ram_start': psutil.virtual_memory().available,
             'packages': {name: importlib.metadata.version(name) for name in ('psutil','pandas','matplotlib','openpyxl')},
             'streamlit_installed': False, 'temperature_sensor': 'not available / not monitored'}}
    dump(run/'audit/preservation_before.json', before)
    dump(run/'audit/start_metadata.json', metadata)
    try:
        source_meta = json.loads((args.phase3/'metadata/run.json').read_text(encoding='utf-8'))
        assert source_meta['data_sha256'] == metadata['dataset_sha256']
        for rel in ('metadata/final_delivery_check.json', 'tests/real_source_delivery_qa.json'):
            assert json.loads((args.phase3/rel).read_text(encoding='utf-8'))['status'] == 'passed'
        catalog_path = args.phase3/'data/public/product_catalog.csv'
        rules_path = args.phase3/'data/public/recommendation_rules_enriched.jsonl'
        links_path = args.phase3/'data/internal/transaction_links.jsonl'
        metadata['input_sha256'] = {str(p.relative_to(ROOT)): sha(p) for p in (catalog_path, rules_path, links_path)}
        catalog = load_catalog(catalog_path)
        original = {r['InvoiceNo']: sorted(set(r['items'])) for r in read_jsonl(DATA)}
        links = read_jsonl(links_path)
        assert len(links) == len(original) and len({r['invoice'] for r in links}) == len(original)
        assert all(sorted(set(r['items'])) == original[r['invoice']] for r in links)
        train, test, split = temporal_split(links)
        assert not {r['invoice'] for r in train} & {r['invoice'] for r in test}
        assert max(r['split_day'] for r in train) < min(r['split_day'] for r in test)
        train_path = run/'internal/train.jsonl'
        jsonl(train_path, [{'items': sorted(set(r['items']))} for r in train])
        counts = Counter(x for r in train for x in set(r['items']))
        dump(run/'training_popularity.json', dict(sorted(counts.items())))
        cases = []
        for i, row in enumerate(test):
            masked = mask_basket(row['items'], args.seed, i)
            if masked:
                cases.append({'case_id': i, 'input': masked[0], 'targets': masked[1]})
        if not cases:
            raise ValueError('No eligible test baskets')
        jsonl(run/'internal/cases.jsonl', cases)
        metadata.update(split=split, evaluation_baskets=len(cases),
            test_singletons_excluded=len(test)-len(cases), training_catalog_size=len(counts),
            cold_test_skus=len({x for r in test for x in r['items']}-counts.keys()),
            cold_target_occurrences=sum(x not in counts for c in cases for x in c['targets']),
            masking_sha256=sha(run/'internal/cases.jsonl'), train_sha256=sha(train_path))
        del links, original, train, test
        first = run_worker(run, train_path, config, 'training_first')
        repeat = run_worker(run, train_path, config, 'training_repeat')
        reproducible = all(sha(first/f) == sha(repeat/f) for f in ('rules.jsonl', 'closed.jsonl'))
        assert reproducible
        training_rules = read_jsonl(first/'rules.jsonl')
        assert all(r['source_dataset_hash'] == sha(train_path) and r['n_transactions']==split['train'] for r in training_rules)
        metadata['training'] = json.loads((first/'worker_result.json').read_text(encoding='utf-8'))
        metadata['reproducible'] = reproducible
        summary = evaluate(cases, training_rules, catalog, counts, config, run)
        audit = independent_metric_check(run, cases, counts)
        dump(run/'audit/independent_metrics.json', audit)
        dump(run/'audit/leakage_check.json', {'status': 'passed', 'strict_day_order': True,
          'disjoint_invoices': True, 'train_counts_only': True,
          'rules_source_hash_is_training_hash': True, 'full_period_catalog_used_only_for_labels': True,
          'full_period_rules_not_used_in_evaluation': True, 'cold_target_occurrences': metadata['cold_target_occurrences']})
        demo_engine = RuleEngine(catalog, read_jsonl(rules_path))
        example = demo_engine.recommend(['22386'], 5)
        dump(run/'demo_example.json', {'mode': 'full-period descriptive only', 'basket': ['22386'], 'recommendations': example})
        charts_and_report(run, metadata, summary, example)
        changed = [rel for rel, digest in before.items() if not (ROOT/rel).is_file() or sha(ROOT/rel)!=digest]
        assert not changed, changed
        dump(run/'audit/preservation_check.json', {'status': 'passed', 'files_preserved': len(before), 'changed': changed})
        metadata['status'] = 'evaluation_completed_demo_qa_pending'
        metadata['total_seconds'] = time.perf_counter()-started
        dump(run/'metadata.json', metadata)
        print(str(run), flush=True)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    except Exception as exc:
        dump(run/'failure.json', {'status': 'failed', 'error': str(exc), 'traceback': traceback.format_exc(),
                                  'elapsed_seconds': time.perf_counter()-started})
        raise


if __name__ == '__main__':
    main()
