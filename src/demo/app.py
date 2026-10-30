"""Local-only Vietnamese thesis demo. Serves an explicit public-data allowlist."""
import argparse
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sys
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src'))
from recommendation.engine import RuleEngine, load_catalog, read_jsonl

DEFAULT_PHASE3 = ROOT/'results/customer_analysis/20261028T162806Z_a0bcc2798234'
ASSETS = {'top_products': 'top_products', 'basket_sizes': 'basket_sizes',
          'monthly_transactions': 'monthly_transactions_coverage', 'rfm_segments': 'rfm_segments'}


def in_windows_appcontainer():
    """Read the current token; never change Windows policy or permissions."""
    if sys.platform != 'win32':
        return False
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE,wintypes.DWORD,ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,
                                           wintypes.DWORD,ctypes.POINTER(wintypes.DWORD)]
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(),0x8,ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        value, size = wintypes.DWORD(), wintypes.DWORD()
        if not advapi.GetTokenInformation(token,29,ctypes.byref(value),ctypes.sizeof(value),ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        return bool(value.value)
    finally:
        kernel.CloseHandle(token)


class LocalDemoServer(ThreadingHTTPServer):
    # HTTPServer defaults to SO_REUSEADDR. On Windows this can allow two live
    # listeners on one port, even across a sandbox boundary. Never share here.
    allow_reuse_address = False
    allow_reuse_port = False

    def server_bind(self):
        if sys.platform == 'win32':
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()


def csv_rows(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


class DemoData:
    """No customer-level source or evaluation cases are opened by this class."""
    def __init__(self, phase3, evaluation):
        self.phase3, self.evaluation = phase3, evaluation
        self.catalog = load_catalog(phase3/'data/public/product_catalog.csv')
        self.full = RuleEngine(self.catalog, read_jsonl(phase3/'data/public/recommendation_rules_enriched.jsonl'))
        self.training = RuleEngine(self.catalog, read_jsonl(evaluation/'training_first/rules.jsonl'))
        self.overview = json.loads((phase3/'reports/final_summary.json').read_text(encoding='utf-8'))
        # Only selected aggregate fields go to frontend, never metadata/git/system paths.
        meta = json.loads((evaluation/'metadata.json').read_text(encoding='utf-8'))
        self.eval_info = {key: meta[key] for key in ('split','evaluation_baskets','test_singletons_excluded',
                          'training_catalog_size','cold_test_skus','cold_target_occurrences','reproducible')}
        self.eval_info['config'] = meta['config']
        self.summaries = csv_rows(evaluation/'metrics_summary.csv')
        self.rfm = csv_rows(phase3/'data/public/rfm_segments.csv')
        self.asset_map = {}
        for name, basename in ASSETS.items():
            image = phase3/f'charts/{basename}.png'
            source = phase3/f'charts/{basename}_data.csv'
            if not image.is_file() or not source.is_file():
                raise FileNotFoundError(f'Chart and matching CSV required: {basename}')
            self.asset_map[name] = (image, source)
        for name in ('precision','recall','hit_rate','catalog_coverage','received'):
            image, source = evaluation/f'charts/{name}.png', evaluation/f'charts/{name}.csv'
            if not image.is_file() or not source.is_file():
                raise FileNotFoundError(f'Chart missing: {name}')
            self.asset_map['eval_'+name] = (image, source)

    def recommend(self, value):
        if not isinstance(value, dict):
            raise ValueError('Yêu cầu phải là JSON object.')
        mode = value.get('mode','full')
        if mode not in ('full','training'):
            raise ValueError('Chế độ không hợp lệ.')
        basket = value.get('basket', [])
        if not isinstance(basket, list) or len(basket)>100 or any(not isinstance(x,str) for x in basket):
            raise ValueError('Giỏ phải là danh sách tối đa 100 StockCode.')
        k = value.get('k',5)
        if k not in (5,10) or type(k) is not int:
            raise ValueError('Demo hỗ trợ Top-5 hoặc Top-10.')
        engine = self.full if mode=='full' else self.training
        rows = engine.recommend(basket,k,float(value.get('confidence',.5)),float(value.get('lift',1)))
        # Explicit projection: do not send any future enrichment fields by accident.
        return {'mode': mode, 'results': [{key:r[key] for key in (
                  'stock_code','description','name_reliable','score','support','confidence','lift')} |
                  {'rule': {key:r['rule'][key] for key in ('rule_id','antecedents','consequents')}} for r in rows]}


def make_handler(data):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, payload, mime='application/json; charset=utf-8'):
            raw = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            if path == '/':
                self.respond(200, (Path(__file__).parent/'index.html').read_bytes(), 'text/html; charset=utf-8')
            elif path == '/api/overview':
                self.respond(200, {key:data.overview[key] for key in ('n_transactions','n_products','n_rules','n_products_with_reliable_names')})
            elif path == '/api/products':
                q = parse_qs(parsed.query).get('q',[''])[0][:200]
                self.respond(200, data.full.search(q,100))
            elif path == '/api/evaluation':
                self.respond(200, {'info':data.eval_info, 'metrics':data.summaries})
            elif path == '/api/analysis':
                self.respond(200, {'rfm':data.rfm, 'charts':list(ASSETS),
                       'rules':[{'antecedents':r['antecedents'],'consequents':r['consequents'],
                                 'support':r['support'],'confidence':r['confidence'],'lift':r['lift']}
                                for r in sorted(data.full.rules,key=lambda r:(-r['support'],r['rule_id']))[:5]]})
            elif path.startswith('/assets/'):
                name = path[len('/assets/'):]
                if name not in data.asset_map:
                    self.respond(404, {'error':'Không tìm thấy biểu đồ.'})
                else:
                    self.respond(200,data.asset_map[name][0].read_bytes(),'image/png')
            else:
                self.respond(404, {'error':'Không tìm thấy tài nguyên.'})

        def do_POST(self):
            if self.path != '/api/recommend':
                self.respond(404, {'error':'Không tìm thấy tài nguyên.'})
                return
            try:
                # Browser requests originate locally; no cross-site POSTs permitted.
                origin = self.headers.get('Origin')
                expected = f'http://127.0.0.1:{self.server.server_port}'
                if origin and origin != expected:
                    self.respond(403, {'error':'Origin không hợp lệ.'})
                    return
                length = int(self.headers.get('Content-Length','0'))
                if not 0 < length <= 16384:
                    raise ValueError('Yêu cầu trống hoặc quá lớn.')
                value = json.loads(self.rfile.read(length))
                self.respond(200, data.recommend(value))
            except (ValueError, TypeError, KeyError) as exc:
                self.respond(400, {'error':str(exc)})
    return Handler


def main():
    parser = argparse.ArgumentParser(description='Demo khóa luận, chỉ truy cập máy này.')
    parser.add_argument('--evaluation',type=Path,required=True)
    parser.add_argument('--phase3',type=Path,default=DEFAULT_PHASE3)
    parser.add_argument('--port',type=int,default=8765)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('--port must be between 0 and 65535 (0 selects a free port).')
    if in_windows_appcontainer():
        parser.error('This process runs inside Windows AppContainer. Start this demo in a normal '
                     'PowerShell session outside the sandbox; Administrator is NOT required. '
                     'A sandbox listener may be unreachable from Edge even when its port is LISTENING.')
    data = DemoData(args.phase3.resolve(),args.evaluation.resolve())
    try:
        server = LocalDemoServer(('127.0.0.1',args.port),make_handler(data))
    except OSError as exc:
        parser.error(f'Cannot bind 127.0.0.1:{args.port}: {exc}. '
                     'Check the owning PID before stopping a verified old demo, '
                     'or select another port with --port. Do not change firewall settings.')
    print(f'Demo: http://127.0.0.1:{server.server_port} - PID {os.getpid()} - Ctrl+C to stop.',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
