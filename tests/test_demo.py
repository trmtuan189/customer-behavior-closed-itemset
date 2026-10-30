"""Real HTTP smoke tests, no browser/visual QA claim."""
import builtins
import csv
import io
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from demo.app import DemoData, LocalDemoServer, make_handler, DEFAULT_PHASE3, in_windows_appcontainer
from demo_fixture import make_demo_fixture


def run_path():
    supplied = os.environ.get('RECOMMENDATION_EVALUATION_RUN')
    if supplied:
        return Path(supplied).resolve()
    found = sorted((ROOT/'results/recommendation_evaluation').glob('*/metadata.json'))
    if not found:
        return None
    return found[-1].parent


class DemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.eval_run = run_path()
        cls.phase3 = Path(os.environ.get('RECOMMENDATION_PHASE3_RUN',str(DEFAULT_PHASE3)))
        if os.environ.get('RECOMMENDATION_DEMO_SYNTHETIC')=='1' or cls.eval_run is None:
            cls.phase3,cls.eval_run = make_demo_fixture(ROOT)
        cls.opened = []
        real_builtin, real_io = builtins.open, io.open
        def track(func):
            def inner(path,*args,**kwargs):
                if isinstance(path,(str,Path)):
                    cls.opened.append(str(path))
                return func(path,*args,**kwargs)
            return inner
        with patch('builtins.open',track(real_builtin)),patch('io.open',track(real_io)):
            cls.data = DemoData(cls.phase3,cls.eval_run)
        cls.server = LocalDemoServer(('127.0.0.1',0),make_handler(cls.data))
        cls.thread = threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def get(self,path):
        with urlopen(self.base+path,timeout=10) as r:
            return r.read(),r.headers

    def post(self,value,headers=None):
        req = Request(self.base+'/api/recommend',data=json.dumps(value).encode(),
                      headers={'Content-Type':'application/json',**(headers or {})})
        with urlopen(req,timeout=10) as r:
            return json.loads(r.read())

    def test_page_four_sections(self):
        raw,h = self.get('/')
        s = raw.decode()
        for section in ('overview','recommend','analysis','evaluation'):
            self.assertIn(f'id="{section}"',s)
        self.assertIn('default-src',h['Content-Security-Policy'])
        self.assertNotIn('https://',s)

    def test_catalog_search(self):
        rows = json.loads(self.get('/api/products?q=22386')[0])
        self.assertEqual(rows[0]['stock_code'],'22386')
        self.assertEqual(json.loads(self.get('/api/products?q=NO_SUCH_PRODUCT_999')[0]),[])

    def test_multi_product_recommendation(self):
        for mode in ('full','training'):
            r = self.post(dict(basket=['22386','21931'],k=10,mode=mode))
            codes = [x['stock_code'] for x in r['results']]
            self.assertTrue(codes)
            self.assertFalse(set(codes)&{'22386','21931'})
            self.assertEqual(len(codes),len(set(codes)))
            self.assertLessEqual(len(codes),10)

    def test_no_rule(self):
        self.assertEqual(self.post(dict(basket=[],k=5))['results'],[])
        code = next(c for c in self.data.catalog if c not in self.data.full.index)
        self.assertEqual(self.post(dict(basket=[code],k=5))['results'],[])

    def test_invalid_requests(self):
        for value in (dict(basket=['ZZZ_INVALID']),dict(basket=['22386'],k=0),
                      dict(basket=['22386'],mode='future'),dict(basket='22386'),
                      dict(basket=['22386'],confidence=1.2),dict(basket=[123])):
            with self.assertRaises(HTTPError) as cm:
                self.post(value)
            self.assertEqual(cm.exception.code,400)

    def test_origin_rejected(self):
        with self.assertRaises(HTTPError) as cm:
            self.post(dict(basket=['22386']),{'Origin':'https://untrusted.example'})
        self.assertEqual(cm.exception.code,403)

    def test_no_internal_files_read(self):
        self.assertFalse([p for p in self.opened if 'internal' in Path(p).parts])
        self.assertFalse([p for p in self.opened if Path(p).name in ('customers.jsonl','rfm.jsonl','transaction_links.jsonl','cases.jsonl')])

    def test_private_routes_not_served(self):
        for path in ('/internal/cases.jsonl','/metadata.json','/assets/../../internal/cases.jsonl','/data/internal/customers.jsonl'):
            with self.assertRaises(HTTPError) as cm:
                self.get(path)
            self.assertEqual(cm.exception.code,404)

    def test_api_no_customer_fields(self):
        values = [json.loads(self.get(p)[0]) for p in ('/api/overview','/api/evaluation','/api/analysis','/api/products?q=22386')]
        values.append(self.post(dict(basket=['22386'])))
        forbidden = {'customer_key','customer_id','Customer ID','invoice','InvoiceNo','country','privacy_key'}
        def scan(v):
            if isinstance(v,dict):
                self.assertFalse(forbidden&v.keys())
                for x in v.values():
                    scan(x)
            elif isinstance(v,list):
                for x in v:
                    scan(x)
        for v in values:
            scan(v)

    def test_metric_api_matches_file(self):
        actual = json.loads(self.get('/api/evaluation')[0])['metrics']
        with open(self.eval_run/'metrics_summary.csv',encoding='utf-8-sig') as f:
            self.assertEqual(actual,list(csv.DictReader(f)))

    def test_chart_source_pairs(self):
        for name,(image,source) in self.data.asset_map.items():
            self.assertTrue(source.is_file())
            raw,h = self.get('/assets/'+name)
            self.assertTrue(raw.startswith(b'\x89PNG'))
            self.assertEqual(raw,image.read_bytes())
            with source.open(encoding='utf-8-sig') as f:
                self.assertTrue(list(csv.DictReader(f)))

    def test_counts_from_verified_summary(self):
        actual = json.loads(self.get('/api/overview')[0])
        self.assertEqual(actual['n_transactions'],self.data.overview['n_transactions'])
        self.assertEqual(actual['n_products'],len(self.data.catalog))
        self.assertEqual(actual['n_rules'],len(self.data.full.rules))

    def test_live_port_cannot_be_shared(self):
        # Try even a legacy SO_REUSEADDR listener against the exclusive socket.
        with self.assertRaises(OSError):
            other = ThreadingHTTPServer(self.server.server_address,make_handler(self.data))
            other.server_close()

    def test_windows_socket_is_exclusive(self):
        import socket
        self.assertFalse(self.server.allow_reuse_address)
        if sys.platform=='win32':
            self.assertEqual(self.server.socket.getsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE),1)

    def test_cli_refuses_appcontainer_before_loading_data(self):
        from demo.app import main
        argv = ['app.py','--evaluation',str(self.eval_run)]
        with patch.object(sys,'argv',argv),patch('demo.app.in_windows_appcontainer',return_value=True),\
             patch('demo.app.DemoData') as load,patch('sys.stderr',new_callable=io.StringIO) as err:
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code,2)
            self.assertIn('Administrator is NOT required',err.getvalue())
            load.assert_not_called()

    def test_cli_occupied_port_message(self):
        from demo.app import main
        argv = ['app.py','--evaluation',str(self.eval_run),'--port',str(self.server.server_port)]
        with patch.object(sys,'argv',argv),patch('demo.app.in_windows_appcontainer',return_value=False),\
             patch('demo.app.DemoData',return_value=self.data),patch('sys.stderr',new_callable=io.StringIO) as err:
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code,2)
            self.assertIn('Cannot bind',err.getvalue())

    def test_current_token_probe(self):
        self.assertIsInstance(in_windows_appcontainer(),bool)


if __name__=='__main__':
    unittest.main()
