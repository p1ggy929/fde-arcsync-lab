"""Real HTTP authentication, cookie sessions, scoped search and request protection."""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from http.cookiejar import CookieJar
from http.server import ThreadingHTTPServer
from pathlib import Path
from arcsync.main import Handler
from arcsync.core.store import init_db

class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.previous=os.environ.get('ARCSYNC_DB')
        os.environ['ARCSYNC_DB']=str(Path(cls.tmp.name)/'http.sqlite3')
        init_db()
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.tmp.cleanup()
        if cls.previous is None: os.environ.pop('ARCSYNC_DB',None)
        else: os.environ['ARCSYNC_DB']=cls.previous
    def setUp(self): self.client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    def request(self,path,data=None,protected=True,origin=None):
        headers={}
        if data is not None:
            headers['Content-Type']='application/json'
            if protected: headers['X-ArcSync-Request']='1'
        if origin: headers['Origin']=origin
        req=urllib.request.Request(self.base+path,data=None if data is None else json.dumps(data).encode(),headers=headers)
        try:
            with self.client.open(req) as r:
                body=r.read();return r.status,json.loads(body) if r.headers['Content-Type'].startswith('application/json') else body
        except urllib.error.HTTPError as e:
            try: return e.code,json.loads(e.read())
            finally: e.close()
    def login(self,role='organizer'): return self.request('/api/login',{'role':role,'password':os.environ.get('ARCSYNC_DEMO_PASSWORD','ArcSync2026!')})
    def test_requires_login(self): self.assertEqual(self.request('/api/state')[0],401)
    def test_login_search_preview_logout(self):
        self.assertEqual(self.login()[0],200)
        status,result=self.request('/api/search?q='+urllib.parse.quote('总体规划'))
        self.assertEqual(status,200);self.assertEqual(len(result['documents']),3)
        self.assertTrue(all(d['level']=='公开' for d in result['documents']))
        self.assertEqual(self.request('/api/documents/YZ-R-1991-002?page=1')[0],404)
        status,result=self.request('/api/documents/YZ-CJ-1992-008?page=6')
        self.assertEqual(status,200);self.assertEqual(result['paper_page'],46);self.assertTrue(result['synthetic'])
        self.assertEqual(self.request('/api/logout',{})[0],200)
        self.assertEqual(self.request('/api/state')[0],401)
    def test_protector_scope(self):
        self.login('protector');_,r=self.request('/api/search')
        self.assertTrue(any(d['level']=='受限' for d in r['documents']))
    def test_bad_credentials_and_cross_site_rejected(self):
        self.assertEqual(self.request('/api/login',{'role':'organizer','password':'wrong'})[0],401)
        self.assertEqual(self.request('/api/login',{},protected=False)[0],403)
        self.assertEqual(self.request('/api/login',{},origin='https://untrusted.example')[0],403)
    def test_static_and_invalid_page(self):
        self.assertEqual(self.request('/')[0],200)
        self.assertEqual(self.request('/discovery-report.md')[0],404)
        self.login()
        self.assertEqual(self.request('/api/documents/YZ-CJ-1992-008?page=999')[0],400)
    def test_unauthorized_mutation(self):
        self.login('representative')
        self.assertEqual(self.request('/api/actions/pause',{'paused':True,'reason':'越权'})[0],403)

if __name__=='__main__': unittest.main()
