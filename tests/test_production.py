"""生产模式、Redis Lua原子配额、身份隔离和恢复回归。"""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from arcsync.core.store import init_db, connect
from arcsync.core.accounts import create_account
from arcsync.core.service import Problem, state, mutate
from arcsync.api.security import identity
from arcsync.production import controls
from arcsync.production.startup import preflight, check_accounts
from arcsync.data_models.config import Endpoint
from arcsync.data_models.providers import ModelClient
from arcsync.main import app

try:import fakeredis
except ImportError:fakeredis=None


class ProductionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'ARCSYNC_ENV':'test','ARCSYNC_DB':self.tmp.name+'/business.sqlite3',
            'ARCSYNC_CHAT_DB':self.tmp.name+'/chat.sqlite3','ARCSYNC_DATABASE_URL':''})
        self.env.start()
    def tearDown(self):self.env.stop();self.tmp.cleanup()
    def test_clean_production_database(self):
        with patch.dict(os.environ,{'ARCSYNC_ENV':'production'}):
            init_db()
            with connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM user_accounts').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM documents').fetchone()[0],0)
            with self.assertRaises(RuntimeError):check_accounts()
            create_account('real-admin','具名管理员','director','a-real-strong-password')
            check_accounts()
    def test_existing_demo_database_rejected(self):
        init_db()
        with patch.dict(os.environ,{'ARCSYNC_ENV':'production'}):
            with self.assertRaises(RuntimeError):check_accounts()
    def test_production_config_fails_closed(self):
        with patch.dict(os.environ,{'ARCSYNC_ENV':'production','ARCSYNC_JWT_SECRET':''}):
            with self.assertRaises(RuntimeError):preflight()
    def test_state_and_mutation_object_authorization(self):
        init_db()
        first=create_account('staff-one','员工甲','representative','a-real-strong-password')
        second=create_account('staff-two','员工乙','representative','a-real-strong-password')
        one=identity('representative','员工甲',first);two=identity('representative','员工乙',second)
        with connect() as db:
            result=mutate(db,one,'create',{'title':'私有事项','applicant':'测试单位','purpose':'测试用途'})
            own=state(db,one);other=state(db,two)
            self.assertIn(result['id'],[b['id'] for b in own['businesses']])
            self.assertNotIn(result['id'],[b['id'] for b in other['businesses']])
            self.assertNotIn(result['id'],[e['business_id'] for e in other['events']])
            with self.assertRaises(Problem):mutate(db,two,'feedback',{'business_id':result['id'],'version':'v1','content':'越权'})
    def test_production_frontend_strips_demo(self):
        with patch.dict(os.environ,{'ARCSYNC_ENV':'production'}):
            # 仅调用资源路由，不启动未配置的生产应用。
            from arcsync.main import frontend
            response=frontend('app.js',None)
            text=response.body.decode()
            self.assertNotIn('默认演示密码',text)
            self.assertNotIn('ArcSync2026!',text)
            self.assertNotIn('account-examples',text)
            Path(self.tmp.name+'/app.js').write_text(text)
            import subprocess
            subprocess.run(['node','--check',self.tmp.name+'/app.js'],check=True,capture_output=True)
    def test_backup_restore_integrity_and_no_overwrite(self):
        spec=importlib.util.spec_from_file_location('backup',Path(__file__).parents[1]/'scripts/production_backup.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        init_db()
        source=self.tmp.name+'/business.sqlite3'
        module.backup(self.tmp.name+'/snapshot',source,source)
        module.restore(self.tmp.name+'/snapshot',self.tmp.name+'/restored')
        with self.assertRaises(FileExistsError):module.restore(self.tmp.name+'/snapshot',self.tmp.name+'/restored')
        with open(self.tmp.name+'/snapshot/chat.sqlite3','ab') as stream:stream.write(b'bad')
        with self.assertRaises(ValueError):module.restore(self.tmp.name+'/snapshot',self.tmp.name+'/invalid')
    def test_readiness_no_model_call(self):
        init_db()
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/ready').status_code,200)

    @unittest.skipUnless(fakeredis,'需安装requirements-dev.txt')
    def test_full_production_startup_and_cookie(self):
        import secrets
        server=fakeredis.FakeRedis(decode_responses=True)
        env={'ARCSYNC_ENV':'production','ARCSYNC_ALLOWED_HOSTS':'testserver',
             'ARCSYNC_REDIS_URL':'redis://:test@localhost:6379/0','ARCSYNC_JWT_SECRET':secrets.token_urlsafe(48),
             'ARCSYNC_INPUT_USD_PER_MILLION':'1','ARCSYNC_OUTPUT_USD_PER_MILLION':'2','ARCSYNC_EMBEDDING_USD_PER_MILLION':'1'}
        with patch.dict(os.environ,env), patch('arcsync.production.controls.redis_client',return_value=server), patch('arcsync.production.startup.redis_client',return_value=server):
            init_db();create_account('real-admin','管理员','director','a-real-strong-password')
            with TestClient(app,base_url='https://testserver') as client:
                response=client.post('/api/login',json={'username':'real-admin','password':'a-real-strong-password'},headers={'X-ArcSync-Request':'1'})
                self.assertEqual(response.status_code,200,response.text)
                self.assertIn('Secure',response.headers['set-cookie'])
                self.assertEqual(client.get('/api/ready').status_code,200)
                self.assertNotIn('ArcSync2026!',client.get('/app.js').text)
                self.assertEqual(client.get('/api/admin/usage').status_code,200)


@unittest.skipUnless(fakeredis,'请安装requirements-dev.txt以运行Redis Lua契约测试')
class RedisControlTests(unittest.TestCase):
    def setUp(self):
        self.redis=fakeredis.FakeRedis(decode_responses=True)
        self.mock=patch('arcsync.production.controls.redis_client',return_value=self.redis);self.mock.start()
        self.env=patch.dict(os.environ,{'ARCSYNC_ENV':'production','ARCSYNC_INPUT_USD_PER_MILLION':'1',
            'ARCSYNC_OUTPUT_USD_PER_MILLION':'2','ARCSYNC_EMBEDDING_USD_PER_MILLION':'1'})
        self.env.start();self.token=controls.actor.set('user-one')
        self.endpoint=Endpoint('ollama','http://127.0.0.1:11434','test')
    def tearDown(self):controls.actor.reset(self.token);self.env.stop();self.mock.stop()
    def test_rate_limit_shared(self):
        controls.rate('same',1,60)
        with self.assertRaises(Problem) as error:controls.rate('same',1,60)
        self.assertEqual(error.exception.status,429)
    def test_concurrency_release_and_expiry(self):
        first=controls.acquire('one')
        with self.assertRaises(Problem):controls.acquire('one')
        with patch.dict(os.environ,{'ARCSYNC_GLOBAL_CONCURRENCY':'1'}):
            with self.assertRaises(Problem):controls.acquire('two')
        controls.release(first)
        second=controls.acquire('one');controls.release(second)
    def test_atomic_budget_and_private_ledger(self):
        with patch.dict(os.environ,{'ARCSYNC_USER_DAILY_TOKENS':'6000'}):
            ticket=controls.reserve(self.endpoint,'chat','secret question',1600)
            with self.assertRaises(Problem):controls.reserve(self.endpoint,'chat','another',1600)
        controls.settle(ticket,'failed')
        summary=controls.usage_summary()
        self.assertEqual(summary['items'][0]['status'],'failed')
        self.assertNotIn('secret question',json.dumps(summary))
        self.assertGreater(summary['reserved_micro_usd'],0)
    def test_missing_identity_and_redis_failure(self):
        controls.actor.set(None)
        with self.assertRaises(Problem) as error:controls.reserve(self.endpoint,'chat','x')
        self.assertEqual(error.exception.status,403)
        with patch('arcsync.production.controls.redis_client',side_effect=OSError()):
            with self.assertRaises(Problem) as error:controls.rate('x',1,60)
            self.assertEqual(error.exception.status,503)
    def test_all_provider_calls_metered(self):
        def respond(request):
            if request.url.path=='/api/embed':return httpx.Response(200,json={'embeddings':[[1,0]]})
            return httpx.Response(200,json={'message':{'content':'ok'},'prompt_eval_count':2,'eval_count':1})
        client=ModelClient(self.endpoint,httpx.MockTransport(respond))
        async def run():
            await client.chat([{'role':'user','content':'x'}])
            await client.embed(['x'])
        asyncio.run(run())
        entries=controls.usage_summary()['items']
        self.assertEqual({e['kind'] for e in entries},{'chat','embed'})
        self.assertTrue(all(e['status']=='completed' for e in entries))
    def test_production_https_and_login_limit(self):
        from arcsync.production.middleware import ProductionMiddleware
        async def target(scope,receive,send):
            from starlette.responses import JSONResponse
            await JSONResponse({'ok':True})(scope,receive,send)
        with patch.dict(os.environ,{'ARCSYNC_ALLOWED_HOSTS':'testserver','ARCSYNC_LOGIN_PER_MINUTE':'1'}):
            client=TestClient(ProductionMiddleware(target),base_url='https://testserver')
            self.assertEqual(client.post('/api/login').status_code,200)
            self.assertEqual(client.post('/api/login').status_code,429)
            self.assertEqual(client.get('http://testserver/').status_code,400)
            self.assertEqual(client.get('https://other/').status_code,400)

    def test_atomic_concurrent_reservations(self):
        from concurrent.futures import ThreadPoolExecutor
        def attempt(index):
            token=controls.actor.set('shared')
            try:
                controls.reserve(self.endpoint,'chat','x',1600)
                return True
            except Problem:return False
            finally:controls.actor.reset(token)
        with patch.dict(os.environ,{'ARCSYNC_USER_DAILY_TOKENS':'6000'}), ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(attempt,range(8))),1)
    def test_stream_usage_and_cancellation_ledger(self):
        async def exercise():
            def respond(request):
                return httpx.Response(200,text='{"message":{"content":"片段"},"done":false}\n{"done":true,"prompt_eval_count":4,"eval_count":2}\n')
            client=ModelClient(self.endpoint,httpx.MockTransport(respond))
            frames=[item async for item in client.stream([{'role':'user','content':'test'}])]
            self.assertEqual(frames[-1]['usage']['output_tokens'],2)
            stream=client.stream([{'role':'user','content':'stop'}]);await anext(stream);await stream.aclose()
        asyncio.run(exercise())
        items=controls.usage_summary()['items']
        self.assertEqual({x['status'] for x in items},{'completed','failed'})
        self.assertTrue(any(x['reported_usage'].get('output_tokens')==2 for x in items))
    def test_expired_lease_and_stale_release(self):
        first=controls.acquire('one')
        with patch('arcsync.production.controls.time.time',return_value=__import__('time').time()+601):
            second=controls.acquire('one')
        controls.release(first)
        with self.assertRaises(Problem):controls.acquire('one')
        controls.release(second)

    def test_middleware_context_and_lease_survive_stream(self):
        from arcsync.production.middleware import ProductionMiddleware
        async def target(scope,receive,send):
            from starlette.responses import StreamingResponse
            async def frames():
                self.assertEqual(controls.actor.get(),'middleware-user')
                self.assertEqual(self.redis.zcard('arcsync:requests'),1)
                yield b'data: ok\n\n'
            await StreamingResponse(frames(),media_type='text/event-stream')(scope,receive,send)
        with patch.dict(os.environ,{'ARCSYNC_ALLOWED_HOSTS':'testserver'}), patch('arcsync.api.security.current_user',return_value={'user_id':'middleware-user'}):
            client=TestClient(ProductionMiddleware(target),base_url='https://testserver')
            response=client.post('/chat')
            self.assertEqual(response.status_code,200)
            self.assertIn('data: ok',response.text)
            client.close()
            self.assertEqual(self.redis.zcard('arcsync:requests'),0)
