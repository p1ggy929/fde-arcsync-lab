"""Account routing, stream authorization and real concurrent ASGI requests."""
import asyncio
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from arcsync.main import app
from arcsync.core.store import connect, init_db
from arcsync.api import routes

HEADERS={'X-ArcSync-Request':'1'}
class ServiceLayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.previous=os.environ.get('ARCSYNC_DB')
        os.environ['ARCSYNC_DB']=str(Path(self.tmp.name)/'service.sqlite3')
        init_db()
        self.client=TestClient(app)
    def tearDown(self):
        self.client.close();self.tmp.cleanup()
        if self.previous is None:os.environ.pop('ARCSYNC_DB',None)
        else:os.environ['ARCSYNC_DB']=self.previous
    def login(self,role='organizer'):
        return self.client.post('/api/login',json={'username':role,'password':os.environ.get('ARCSYNC_DEMO_PASSWORD','ArcSync2026!')},headers=HEADERS)
    def test_account_decides_portal_and_backend_access(self):
        for role in ('organizer','representative','reviewer','engineer','protector','director'):
            with self.subTest(role=role):
                response=self.login(role);self.assertEqual(response.status_code,200)
                employee=role in ('organizer','representative')
                self.assertEqual(response.json()['portal'],'employee' if employee else 'admin')
                self.assertEqual(self.client.get('/api/admin/overview').status_code,403 if employee else 200)
                wrong='/admin' if employee else '/portal'
                self.assertEqual(self.client.get(wrong,follow_redirects=False).headers['location'],response.json()['home'])
        self.client.post('/api/logout',json={},headers=HEADERS)
        self.assertEqual(self.client.get('/admin',follow_redirects=False).headers['location'],'/')
    def test_server_ignores_forged_portal_and_rotates_session(self):
        self.login();old=self.client.cookies.get('arcsync_session')
        r=self.client.post('/api/login',json={'username':'organizer','password':os.environ.get('ARCSYNC_DEMO_PASSWORD','ArcSync2026!'),'portal':'admin'},headers=HEADERS)
        self.assertEqual(r.json()['portal'],'employee')
        with connect() as db:self.assertIsNone(db.execute('SELECT * FROM sessions WHERE token=?',(old,)).fetchone())
        self.assertIn('HttpOnly',r.headers['set-cookie']);self.assertIn('SameSite=strict',r.headers['set-cookie'])
    def test_stream_scope_matches_search_and_logout_revokes(self):
        self.assertEqual(self.client.get('/api/search/stream').status_code,401)
        self.login()
        response=self.client.get('/api/search/stream?q=总体规划')
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.headers['content-type'].startswith('text/event-stream'))
        self.assertIn('event: start',response.text);self.assertIn('event: done',response.text)
        docs=[json.loads(frame.split('data: ',1)[1]) for frame in response.text.split('\n\n') if frame.startswith('event: document')]
        self.assertEqual(docs,self.client.get('/api/search?q=总体规划').json()['documents'])
        self.assertNotIn('YZ-R-1991-002',response.text)
        self.client.post('/api/logout',json={},headers=HEADERS)
        self.assertEqual(self.client.get('/api/search/stream').status_code,401)
    def test_expiry_rechecked_before_streaming_document(self):
        self.login()
        with patch.object(routes,'current_user',side_effect=lambda request: (_ for _ in ()).throw(routes.Problem('expired',401))):
            response=self.client.get('/api/search/stream')
        self.assertIn('event: expired',response.text)
        self.assertNotIn('event: document',response.text)
    def test_input_limits_and_offline_documentation(self):
        self.assertEqual(self.client.post('/api/login',content='x'*32769,headers={**HEADERS,'Content-Type':'application/json'}).status_code,413)
        self.assertEqual(self.client.post('/api/login',json=[],headers=HEADERS).status_code,400)
        self.assertEqual(self.client.get('/api/docs').status_code,200)
        schema=self.client.get('/api/openapi.json').json()
        self.assertIn('/api/search/stream',schema['paths'])
        self.assertEqual({p['name'] for p in schema['paths']['/api/search/stream']['get']['parameters']},{'q','category','year','status'})
        self.login()
        self.assertEqual(self.client.get('/api/search/stream',params={'q':'x'*201}).status_code,400)
    def test_concurrent_writes_only_one_revision_wins(self):
        self.login();token=self.client.cookies.get('arcsync_session')
        async def exercise():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver',cookies={'arcsync_session':token}) as client:
                responses=await asyncio.gather(*[client.post('/api/actions/note',json={'business_id':'CY-2026-1023-001','revision':1,'note':f'并发操作 {i}'},headers=HEADERS) for i in range(8)])
                return [r.status_code for r in responses]
        codes=asyncio.run(exercise())
        self.assertEqual(codes.count(200),1);self.assertEqual(codes.count(409),7)
    def test_slow_search_does_not_block_health_request(self):
        self.login();token=self.client.cookies.get('arcsync_session')
        started=threading.Event();release=threading.Event();original=routes.search_documents
        def slow(*args):
            started.set()
            if not release.wait(5):raise AssertionError('health did not finish while search was pending')
            return original(*args)
        async def exercise():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver',cookies={'arcsync_session':token}) as client:
                task=asyncio.create_task(client.get('/api/search/stream'))
                try:
                    for _ in range(100):
                        if started.is_set():break
                        await asyncio.sleep(.01)
                    self.assertTrue(started.is_set())
                    result=await asyncio.wait_for(client.get('/api/health'),timeout=1)
                    self.assertEqual(result.status_code,200)
                    self.assertFalse(task.done())
                finally:release.set()
                self.assertEqual((await task).status_code,200)
        with patch.object(routes,'search_documents',side_effect=slow):asyncio.run(exercise())

if __name__=='__main__':unittest.main()
