"""Real FAISS + model HTTP protocol fixtures; no credentials or real inference."""
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
from arcsync.main import app
from arcsync.core.store import connect, init_db
from arcsync.data_models.config import Endpoint, ModelSettings, ModelError
from arcsync.data_models.providers import ModelClient
from arcsync.data_models.knowledge import import_document, available_chunks
from arcsync.data_models.vectors import hybrid_search
from arcsync.agents.orchestrator import Orchestrator
from arcsync.agents.tools import ToolBox
from arcsync.agents.repository import detail
from arcsync.core.service import Problem

USER={'role':'organizer','name':'林亦舟','label':'档案整理员'}
ENGINEER={'role':'engineer','name':'秦芮'}
PROTECTOR={'role':'protector','name':'傅景'}
HEADERS={'X-ArcSync-Request':'1'}

class ProviderFixture:
    def __init__(self,invalid_citation=False,repeated=False,unknown=False):
        self.calls=[];self.tool_round=0;self.invalid_citation=invalid_citation;self.repeated=repeated;self.unknown=unknown
        self.embed_inputs=[]
    def __call__(self,request):
        body=json.loads(request.content);self.calls.append((request.url.path,body))
        if request.url.path.endswith('/embed') or request.url.path.endswith('/embeddings'):
            self.embed_inputs.extend(body['input'])
            vectors=[[1.,0.,.1] if '规划' in text else [0.,1.,.1] for text in body['input']]
            if request.url.path.endswith('/embed'):return httpx.Response(200,json={'embeddings':vectors})
            return httpx.Response(200,json={'data':[{'index':i,'embedding':v} for i,v in enumerate(vectors)]})
        ollama=request.url.path.endswith('/api/chat')
        if body.get('stream'):
            text='依据目录，需要检查后续更正 [C999]。' if self.invalid_citation else '依据目录，需要检查后续更正 [C1]。'
            if ollama:
                data=[{'message':{'content':text},'done':False},{'message':{'content':''},'done':True,'prompt_eval_count':20,'eval_count':8}]
                return httpx.Response(200,content='\n'.join(json.dumps(d,ensure_ascii=False) for d in data)+'\n')
            return httpx.Response(200,content='data: '+json.dumps({'choices':[{'delta':{'content':text},'finish_reason':None}]},ensure_ascii=False)+'\n\ndata: [DONE]\n\n')
        if body.get('format') or body.get('response_format'):
            msg={'content':json.dumps({'intent':'retrieve','query':'总体规划','business_id':'','category':'','year':None})}
        elif body.get('tools'):
            self.tool_round+=1
            if self.tool_round==1 or self.repeated:
                args={'document_id':'YZ-CJ-1986-001','scan_page':6}
                msg={'content':'','tool_calls':[{'id':'call_page','type':'function','function':{'name':'run_sql' if self.unknown else 'read_archive_page','arguments':args if ollama else json.dumps(args)}}]}
            else:msg={'content':'证据已足够。'}
        else:msg={'content':'连接正常。'}
        if ollama:return httpx.Response(200,json={'message':msg,'done':True})
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant',**msg},'finish_reason':'tool_calls' if msg.get('tool_calls') else 'stop'}]})

class ModelLayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'ARCSYNC_DATABASE_URL':'','ARCSYNC_DB':str(Path(self.tmp.name)/'test.sqlite3'),
                                      'ARCSYNC_VECTOR_DIR':str(Path(self.tmp.name)/'vectors'),'ARCSYNC_LLM_PROVIDER':'disabled',
                                      'ARCSYNC_LLM_MODEL':'','ARCSYNC_EMBEDDING_MODEL':''})
        self.env.start();init_db()
        self.client=TestClient(app)
    def tearDown(self):
        self.client.close();self.env.stop();self.tmp.cleanup()
    def config(self,provider='ollama'):
        base='http://127.0.0.1:11434' if provider=='ollama' else 'http://127.0.0.1:8000/v1'
        return ModelSettings(Endpoint(provider,base,'fixture-chat','test-key'),Endpoint(provider,base,'fixture-embed','test-key'))
    def agent(self,fixture,provider='ollama'):
        config=self.config(provider);transport=httpx.MockTransport(fixture)
        return Orchestrator(USER,config,ModelClient(config.llm,transport),ModelClient(config.embedding,transport))
    def login(self,role='organizer'):
        return self.client.post('/api/login',json={'username':role,'password':os.getenv('ARCSYNC_DEMO_PASSWORD','ArcSync2026!')},headers=HEADERS)
    def collect(self,agent):
        async def go():return [event async for event in agent.run('查县城总体规划与后续更正')]
        return asyncio.run(go())
    def test_disabled_is_explicit_and_not_fake_answer(self):
        self.login()
        status=self.client.get('/api/models/status').json()
        self.assertFalse(status['configured'])
        response=self.client.post('/api/agent/chat/stream',json={'question':'找规划'},headers=HEADERS)
        self.assertEqual(response.status_code,503);self.assertIn('尚未配置',response.json()['error'])
        with connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM agent_runs').fetchone()[0],0)
    def test_full_native_tool_loop_faiss_generation_and_persistence(self):
        fixture=ProviderFixture();events=self.collect(self.agent(fixture))
        self.assertEqual(events[-1]['event'],'done',events)
        self.assertTrue(any(e['event']=='tool' and e['data']['name']=='read_archive_page' for e in events))
        result=events[-1]['data'];self.assertIn('[C1]',result['answer'])
        self.assertTrue(all(s.get('document_id')!='YZ-R-1991-002' for s in result['sources']))
        self.assertTrue(list((Path(self.tmp.name)/'vectors').glob('*.faiss')))
        saved=detail(USER,result['run_id']);self.assertEqual(saved['status'],'completed')
        self.assertGreater(saved['usage']['model_calls'],2)
        with self.assertRaises(Problem):detail(ENGINEER,result['run_id'])
        tool_requests=[b for _,b in fixture.calls if b.get('tools')]
        self.assertIsInstance(tool_requests[-1]['messages'][3]['tool_calls'][0]['function']['arguments'],dict)
    def test_openai_compatible_cloud_and_vllm_protocol(self):
        for provider in ('cloud','vllm'):
            with self.subTest(provider=provider):
                events=self.collect(self.agent(ProviderFixture(),provider))
                self.assertEqual(events[-1]['event'],'done',events)
    def test_fabricated_citation_discards_answer(self):
        events=self.collect(self.agent(ProviderFixture(invalid_citation=True)))
        self.assertEqual(events[-1]['event'],'error');self.assertTrue(events[-1]['data']['discard_draft'])
        with connect() as db:
            run=db.execute('SELECT * FROM agent_runs WHERE id=?',(events[-1]['data']['run_id'],)).fetchone()
            self.assertEqual(run['answer'],'');self.assertEqual(run['status'],'failed')
    def test_repeat_tools_are_bounded(self):
        events=self.collect(self.agent(ProviderFixture(repeated=True)))
        self.assertEqual(events[-1]['event'],'error');self.assertIn('重复',events[-1]['data']['error'])
    def test_unknown_tool_is_rejected_not_executed(self):
        events=self.collect(self.agent(ProviderFixture(unknown=True)))
        self.assertTrue(any(e['event']=='tool' and e['data'].get('status')=='rejected' for e in events))
        with connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM documents').fetchone()[0],8)
    def test_authorization_before_embedding_and_cache_reuse(self):
        fixture=ProviderFixture();config=self.config();embed=ModelClient(config.embedding,httpx.MockTransport(fixture))
        asyncio.run(hybrid_search(USER,'规划',embed))
        first=len(fixture.embed_inputs)
        self.assertFalse(any('内部专项' in t for t in fixture.embed_inputs))
        asyncio.run(hybrid_search(USER,'规划',embed))
        self.assertEqual(len(fixture.embed_inputs),first+1)
        with connect() as db:db.execute("UPDATE documents SET level='受限' WHERE id='YZ-CJ-1986-001'")
        result=asyncio.run(hybrid_search(USER,'规划',embed))
        self.assertFalse(any(c['document_id']=='YZ-CJ-1986-001' for c in result))
    def test_restricted_context_requires_local_opt_in_and_role(self):
        self.assertFalse(self.config().allow_restricted)
        config=ModelSettings(self.config().llm,self.config().embedding,restricted_context=True)
        self.assertTrue(config.allow_restricted)
        self.assertTrue(any(c['document_id']=='YZ-R-1991-002' for c in available_chunks(PROTECTOR,True)))
        self.assertFalse(any(c['document_id']=='YZ-R-1991-002' for c in available_chunks(USER,True)))
        cloud=self.config('cloud')
        self.assertFalse(ModelSettings(cloud.llm,cloud.embedding,restricted_context=True).allow_restricted)
    def test_version_change_blocks_stored_answer(self):
        events=self.collect(self.agent(ProviderFixture()));identifier=events[-1]['data']['run_id']
        with connect() as db:db.execute("UPDATE documents SET version='changed' WHERE id='YZ-CJ-1986-001'")
        with self.assertRaises(Problem):detail(USER,identifier)
    def test_pause_blocks_agent_and_resume_rules_unchanged(self):
        self.login()
        with connect() as db:db.execute("UPDATE settings SET value='true' WHERE key='paused'")
        config=self.config()
        with patch('arcsync.api.agent_routes.ModelSettings.load',return_value=config):
            response=self.client.post('/api/agent/chat/stream',json={'question':'找规划'},headers=HEADERS)
        self.assertEqual(response.status_code,409)
    def test_http_stream_contract_and_session_guard(self):
        self.login();fixture=ProviderFixture();agent=self.agent(fixture)
        with patch('arcsync.api.agent_routes.Orchestrator',return_value=agent):
            r=self.client.post('/api/agent/chat/stream',json={'question':'查规划'},headers=HEADERS)
        self.assertEqual(r.status_code,200);self.assertIn('event: done',r.text)
        self.assertIn('event: token',r.text);self.assertIn('event: tool',r.text)
        self.assertNotIn('YZ-R-1991-002',r.text)
        self.client.post('/api/logout',json={},headers=HEADERS)
        self.assertEqual(self.client.get('/api/agent/runs').status_code,401)
    def test_cancel_records_cancelled_run(self):
        async def go():
            stream=self.agent(ProviderFixture()).run('查规划')
            event=await anext(stream);await stream.aclose();return event['data']['run_id']
        identifier=asyncio.run(go())
        with connect() as db:self.assertEqual(db.execute('SELECT status FROM agent_runs WHERE id=?',(identifier,)).fetchone()[0],'cancelled')
    def test_import_permission_version_preservation_and_page_read(self):
        doc={'id':'KS-TEST-001','title':'测试规划资料','category':'城市建设','year':2026,'version':'v1','level':'公开',
             'pages':[{'scan_page':1,'paper_page':11,'text':'本项目用地调整位于东片区。'}]}
        self.login()
        self.assertEqual(self.client.post('/api/admin/knowledge/documents',json=doc,headers=HEADERS).status_code,403)
        self.login('engineer');self.assertEqual(self.client.post('/api/admin/knowledge/documents',json=doc,headers=HEADERS).status_code,200)
        r=self.client.get('/api/documents/KS-TEST-001?page=1').json();self.assertFalse(r['synthetic']);self.assertIn('东片区',r['paragraphs'][0])
        self.assertEqual(self.client.post('/api/admin/knowledge/documents',json=doc,headers=HEADERS).status_code,409)
        updated={**doc,'version':'v2','expected_version':'v1'}
        self.assertEqual(self.client.post('/api/admin/knowledge/documents',json=updated,headers=HEADERS).status_code,200)
        with connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM document_chunks WHERE document_id=?',('KS-TEST-001',)).fetchone()[0],2)
        self.assertEqual(self.client.post('/api/admin/knowledge/documents',json={**doc,'id':'SECRET','level':'受限'},headers=HEADERS).status_code,403)
    def test_import_cannot_clear_existing_quality_flag(self):
        doc={'id':'YZ-CJ-1987-009','title':'修复文本','category':'城市建设','year':1987,'version':'v2',
             'expected_version':'v1.0','level':'公开','pages':[{'scan_page':1,'paper_page':13,'text':'未经审核的修复稿'}]}
        with self.assertRaises(Problem):import_document(ENGINEER,doc)
        with connect() as db:self.assertEqual(db.execute('SELECT status FROM documents WHERE id=?',(doc['id'],)).fetchone()[0],'待修复')
    def test_session_revocation_stops_orchestration(self):
        checks=0
        async def guard():
            nonlocal checks
            checks+=1
            if checks>=2:raise Problem('会话已失效',401)
        async def run():return [e async for e in self.agent(ProviderFixture()).run('查规划',guard)]
        events=asyncio.run(run())
        self.assertEqual(events[-1]['event'],'error');self.assertIn('会话',events[-1]['data']['error'])
        self.assertFalse(any(e['event']=='token' for e in events))
    def test_missing_imported_page_cannot_be_confirmed_as_citation(self):
        import_document(ENGINEER,{'id':'KS-HOLE','title':'稀疏页文本','category':'城市建设','year':2026,'version':'v1',
            'pages':[{'scan_page':2,'paper_page':12,'text':'仅提供扫描第二页'}]})
        self.login()
        response=self.client.post('/api/actions/cite',json={'business_id':'CY-2026-1023-001','revision':1,
             'document_id':'KS-HOLE','version':'v1','scan_page':1,'paper_page':11},headers=HEADERS)
        self.assertEqual(response.status_code,409)
    def test_document_changed_after_approval_blocks_delivery(self):
        self.login('reviewer')
        self.assertEqual(self.client.post('/api/actions/approve',json={'business_id':'CY-2026-1023-002','revision':1,'note':'依据已核对'},headers=HEADERS).status_code,200)
        import_document(ENGINEER,{'id':'YZ-CJ-1992-008','title':'更新的竣工记录','category':'城市建设','year':1992,
            'version':'v2','expected_version':'v1.1','pages':[{'scan_page':1,'paper_page':41,'text':'审核后发布了新版本'}]})
        response=self.client.post('/api/actions/deliver',json={'business_id':'CY-2026-1023-002','revision':2,'note':'交付'},headers=HEADERS)
        self.assertEqual(response.status_code,409)
    def test_invalid_vectors_fail_closed(self):
        # httpx JSON serialization itself rejects NaN; provide literal JSON for the protocol test.
        transport=httpx.MockTransport(lambda request:httpx.Response(200,content='{"embeddings":[[NaN,0]]}'))
        with self.assertRaises(ModelError):asyncio.run(ModelClient(self.config().embedding,transport).embed(['x']))
    def test_mysql_parameter_adapter_and_transaction_cleanup(self):
        from arcsync.data_models.mysql import MySQLConnection
        class Cursor:
            description=None;lastrowid=7;rowcount=1
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def execute(self,sql,args):self.sql=sql;self.args=args;calls.append((sql,args))
            def fetchone(self):return {'locked':1}
        class Connection:
            def cursor(self):return Cursor()
            def begin(self):calls.append(('begin',()))
            def commit(self):calls.append(('commit',()))
            def rollback(self):calls.append(('rollback',()))
            def close(self):calls.append(('close',()))
        calls=[]
        with patch('arcsync.data_models.mysql.pymysql.connect',return_value=Connection()):
            with MySQLConnection('mysql://user:password@127.0.0.1/arcsync_test') as db:
                db.execute('BEGIN IMMEDIATE');db.execute('UPDATE settings SET value=? WHERE key=?',('值','paused'))
        self.assertIn(('UPDATE settings SET value=%s WHERE `key`=%s',('值','paused')),calls)
        self.assertTrue(any('RELEASE_LOCK' in sql for sql,_ in calls));self.assertIn(('commit',()),calls)

if __name__=='__main__':unittest.main()
