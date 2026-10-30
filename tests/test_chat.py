"""多轮聊天验收：隔离身份、真实SQLite事务、上下文、SSE与错误契约。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from starlette.requests import Request
from arcsync.main import app
from arcsync.api.security import current_user
from arcsync.core.store import connect, init_db
from arcsync.core.accounts import create_account
from arcsync.chat import repository, routes
from arcsync.core.service import Problem
from arcsync.data_models.knowledge import available_chunks
from arcsync.agents.tools import ToolBox

HEADERS={'X-ArcSync-Request':'1'}
PASSWORD='Chat-Test-Password-2026!'


def events(response):
    result=[]
    for frame in response.text.split('\n\n'):
        if not frame.startswith('event:'):continue
        lines=frame.split('\n')
        result.append((lines[0][7:],json.loads(lines[1][6:])))
    return result


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'ARCSYNC_DB':str(Path(self.tmp.name)/'business.sqlite3'),
                                      'ARCSYNC_DATABASE_URL':'','ARCSYNC_CHAT_DB':str((Path(self.tmp.name)/'chat.sqlite3').resolve()),
                                      'ARCSYNC_LLM_PROVIDER':'disabled','ARCSYNC_LLM_MODEL':''})
        self.env.start();init_db()
        self.user_id=create_account('employee.a','员工甲','organizer',PASSWORD)
        self.other_id=create_account('employee.b','员工乙','organizer',PASSWORD)
        self.client=TestClient(app);self.other=TestClient(app)
        self.login(self.client,'employee.a');self.login(self.other,'employee.b')
        self.user=self.client.get('/api/session').json();self.other_user=self.other.get('/api/session').json()
        self.histories=[];self.stream_failure=False;self.slow=False
        outer=self
        class FakeAgent:
            """测试返回样本；生产代码没有启用该替身的配置入口。"""
            def __init__(self,user):self.user=user
            async def run(self,question,guard=None,history=None,persist=True):
                outer.assertFalse(persist)
                outer.histories.append([dict(m) for m in history or []])
                if guard:await guard()
                yield {'event':'step','data':{'stage':'retrieval'}}
                if outer.slow:await asyncio.sleep(20)
                yield {'event':'step','data':{'stage':'generate'}}
                yield {'event':'token','data':{'text':'测试草稿'}}
                if outer.stream_failure:
                    yield {'event':'error','data':{'error':'模型连接中断，请重新提问'}};return
                chunk=available_chunks(self.user)[0]
                source=ToolBox(self.user,None,False).remember(chunk)
                answer='这是'+question+'的测试回答 [C1]。'
                yield {'event':'done','data':{'answer':answer,'sources':[source],'usage':{}}}
        self.agent_patch=patch.object(routes,'Orchestrator',FakeAgent);self.agent_patch.start()
    def tearDown(self):
        self.agent_patch.stop();self.client.close();self.other.close();self.env.stop();self.tmp.cleanup()
    def login(self,client,username):
        return client.post('/api/login',json={'username':username,'password':PASSWORD},headers=HEADERS)
    def ask(self,question='第一轮问题',session_id=None,client=None):
        return (client or self.client).post('/chat',json={'session_id':session_id,'question':question},headers=HEADERS)
    def session(self,response):return events(response)[0][1]['data']['session_id']
    def test_named_accounts_have_distinct_identity_with_same_role(self):
        self.assertEqual(self.user['role'],self.other_user['role']);self.assertNotEqual(self.user['user_id'],self.other_user['user_id'])
        self.assertEqual(self.user['user_id'],self.user_id)
        self.assertEqual(self.login(self.client,'employee.a').json()['user_id'],self.user_id)
    def test_chat_sse_envelope_and_sqlite_persistence(self):
        response=self.ask();self.assertEqual(response.status_code,200)
        self.assertTrue(response.headers['content-type'].startswith('text/event-stream'))
        stream=events(response);names=[name for name,_ in stream]
        self.assertEqual(names[0],'session');self.assertEqual(names[-2:],['citations','done'])
        self.assertIn('delta',names);self.assertTrue(all(item['code']==0 and item['message'] for _,item in stream))
        session_id=self.session(response)
        with repository.chat_connect() as db:
            saved=db.execute('SELECT * FROM chat_messages WHERE session_id=? ORDER BY role',(session_id,)).fetchall()
        self.assertEqual(len(saved),2);self.assertTrue(all(row['status']=='completed' for row in saved))
        self.assertIn('[C1]',next(row['content'] for row in saved if row['role']=='assistant'))
        # 新聊天不能复制到按岗位共享的旧agent_runs里。
        with connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM agent_runs').fetchone()[0],0)
    def test_last_six_complete_rounds_only(self):
        identifier=None
        for number in range(1,9):
            response=self.ask(f'问题{number}',identifier);self.assertEqual(response.status_code,200)
            self.assertEqual(events(response)[-1][0],'done');identifier=self.session(response)
        history=self.histories[-1]
        self.assertEqual(len(history),12)
        self.assertEqual([m['content'] for m in history if m['role']=='user'],[f'问题{i}' for i in range(2,8)])
    def test_wrong_user_cannot_read_or_continue_session(self):
        identifier=self.session(self.ask())
        for response in (self.other.get('/chat/sessions/'+identifier+'/messages'),self.ask('越权追问',identifier,self.other)):
            self.assertEqual(response.status_code,404);self.assertEqual(response.json()['code'],40401)
        self.assertEqual(self.other.get('/chat/sessions').json()['data']['sessions'],[])
    def test_unknown_and_forbidden_sessions_have_same_response(self):
        identifier=self.session(self.ask())
        denied=self.other.get('/chat/sessions/'+identifier+'/messages')
        missing=self.other.get('/chat/sessions/'+'f'*32+'/messages')
        self.assertEqual(denied.json(),missing.json())
    def test_all_preflight_errors_are_chinese_json(self):
        examples=[({'session_id':None,'question':''},400),({'session_id':None,'question':4},400),
                  ({'session_id':'invalid','question':'测试'},400),({'session_id':None,'question':'x'*2001},400),
                  ({'session_id':None,'question':'测试','history':[]},400)]
        for body,code in examples:
            with self.subTest(body=str(body)[:30]):
                response=self.client.post('/chat',json=body,headers=HEADERS)
                self.assertEqual(response.status_code,code);self.assertEqual(response.json()['code'],40001)
                self.assertTrue(any('\u4e00'<=c<='\u9fff' for c in response.json()['message']))
        with TestClient(app) as anonymous:
            response=anonymous.post('/chat',json={'session_id':None,'question':'测试'},headers=HEADERS)
            self.assertEqual(response.status_code,401);self.assertEqual(response.json()['code'],40101)
        self.assertEqual(self.client.get('/chat').json()['code'],40401)
        self.assertEqual(self.client.put('/chat').json()['code'],40501)
    def test_unconfigured_model_does_not_create_phantom_session(self):
        self.agent_patch.stop()
        try:
            response=self.ask();self.assertEqual(response.status_code,503);self.assertEqual(response.json()['code'],50301)
            self.assertEqual(self.client.get('/chat/sessions').json()['data']['sessions'],[])
        finally:self.agent_patch.start()
    def test_stream_failure_discards_draft_and_excludes_round(self):
        self.stream_failure=True;response=self.ask();identifier=self.session(response)
        stream=events(response);self.assertEqual(stream[-1][0],'error');self.assertEqual(stream[-1][1]['code'],50302)
        self.assertNotIn('done',[name for name,_ in stream])
        self.assertTrue(stream[-1][1]['data']['discard_draft'])
        data=self.client.get('/chat/sessions/'+identifier+'/messages').json()['data']['messages']
        self.assertEqual(data[0]['role'],'user');self.assertEqual(data[1]['status'],'failed');self.assertEqual(data[1]['content'],'')
        self.stream_failure=False;self.ask('失败后的新问题',identifier)
        self.assertEqual(self.histories[-1],[])
    def test_same_session_busy_and_database_concurrent_reservation(self):
        turn=repository.start_turn(self.user,None,'尚未生成')
        response=self.ask('重复请求',turn['session_id']);self.assertEqual(response.status_code,409)
        self.assertEqual(response.json()['code'],40901)
        repository.finish(self.user,turn,'cancelled')
        def start(_):
            try:return repository.start_turn(self.user,turn['session_id'],'并发问题')
            except Problem as error:return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(start,range(2)))
        self.assertEqual(sum(isinstance(result,dict) for result in results),1);self.assertIn(409,results)
    def test_rate_limit_returns_error_code(self):
        identifier=None
        for _ in range(12):
            turn=repository.start_turn(self.user,identifier,'速率测试');identifier=turn['session_id'];repository.finish(self.user,turn,'failed')
        response=self.ask('第十三次',identifier)
        self.assertEqual(response.status_code,429);self.assertEqual(response.json()['code'],42901)
    def test_timeout_saved_as_failed(self):
        self.slow=True
        with patch.object(routes,'TASK_TIMEOUT',.03):response=self.ask()
        stream=events(response);self.assertEqual(stream[-1][0],'error');self.assertEqual(stream[-1][1]['code'],50302)
        self.assertIn('超时',stream[-1][1]['message'])
        data=self.client.get('/chat/sessions/'+self.session(response)+'/messages').json()['data']['messages']
        self.assertEqual(data[-1]['status'],'failed')
    def test_disconnect_cancels_and_releases_busy_state(self):
        token=self.client.cookies.get('arcsync_session')
        async def run():
            async def receive():return {'type':'http.request','body':b'','more_body':False}
            request=Request({'type':'http','method':'POST','scheme':'http','path':'/chat','query_string':b'',
                             'headers':[(b'cookie',('arcsync_session='+token).encode())]},receive)
            response=await routes.chat(request,{'session_id':None,'question':'取消测试'},self.user)
            first=await anext(response.body_iterator)
            await response.body_iterator.aclose()
            return json.loads(first.split('data: ',1)[1])['data']['session_id']
        identifier=asyncio.run(run())
        data=self.client.get('/chat/sessions/'+identifier+'/messages').json()['data']['messages']
        self.assertEqual(data[-1]['status'],'cancelled')
        self.assertEqual(events(self.ask('取消后重试',identifier))[-1][0],'done')
    def test_source_change_hides_saved_answer_and_omits_history(self):
        identifier=self.session(self.ask())
        with connect() as db:db.execute("UPDATE documents SET level='受限'")
        data=self.client.get('/chat/sessions/'+identifier+'/messages').json()['data']['messages']
        self.assertEqual(data[-1]['status'],'unavailable');self.assertEqual(data[-1]['content'],'');self.assertEqual(data[-1]['citations'],[])
        self.assertEqual(repository.history(self.user,identifier,99),[])
    def test_filename_and_snippet_are_returned(self):
        with connect() as db:db.execute('INSERT INTO document_files VALUES(?,?,?)',('YZ-CJ-1986-001','v2.0','员工休假管理办法.pdf'))
        stream=events(self.ask());references=next(body['data']['items'] for name,body in stream if name=='citations')
        self.assertEqual(references[0]['filename'],'员工休假管理办法.pdf');self.assertTrue(references[0]['snippet'])
        self.assertIsNone(references[0]['page']) # 合成目录不能伪造纸质页码。
    def test_paginated_history_is_stable_and_ordered(self):
        identifier=None
        for number in range(4):identifier=self.session(self.ask(f'分页{number}',identifier))
        latest=self.client.get('/chat/sessions/'+identifier+'/messages?limit=2').json()['data']
        self.assertEqual([m['turn_no'] for m in latest['messages']],[3,3,4,4]);self.assertTrue(latest['has_more'])
        older=self.client.get('/chat/sessions/'+identifier+'/messages?limit=2&before_turn=3').json()['data']
        self.assertEqual([m['turn_no'] for m in older['messages']],[1,1,2,2]);self.assertFalse(older['has_more'])
    def test_history_budget_removes_whole_rounds(self):
        turn=repository.start_turn(self.user,None,'长问题'*100)
        repository.finish(self.user,turn,'completed','长回答'*100,[],[])
        self.assertEqual(repository.history(self.user,turn['session_id'],2,max_chars=10),[])
    def test_disabled_account_revokes_existing_session(self):
        with connect() as db:db.execute('UPDATE user_accounts SET enabled=0 WHERE user_id=?',(self.user_id,))
        response=self.client.get('/chat/sessions');self.assertEqual(response.status_code,401)
    def test_restart_preserves_user_ids_and_conversations(self):
        identifier=self.session(self.ask());init_db()
        self.assertEqual(self.client.get('/api/session').json()['user_id'],self.user_id)
        self.assertEqual(len(self.client.get('/chat/sessions/'+identifier+'/messages').json()['data']['messages']),2)
    def test_chat_page_assets_and_existing_portal_entry(self):
        self.assertEqual(self.client.get('/chat-ui').status_code,200)
        self.assertEqual(self.client.get('/chat.js').status_code,200)
        self.assertEqual(self.client.get('/chat.css').status_code,200)
        self.assertIn('/chat-ui',self.client.get('/app.js').text)
    def test_history_reaches_real_model_adapter_at_each_stage(self):
        from test_model_layers import ProviderFixture
        from arcsync.agents.orchestrator import Orchestrator
        from arcsync.data_models.config import Endpoint,ModelSettings
        from arcsync.data_models.providers import ModelClient
        import httpx
        fixture=ProviderFixture();transport=httpx.MockTransport(fixture)
        config=ModelSettings(Endpoint('ollama','http://127.0.0.1:11434','fixture-chat'),Endpoint('ollama','http://127.0.0.1:11434','fixture-embed'))
        agent=Orchestrator(self.user,config,ModelClient(config.llm,transport),ModelClient(config.embedding,transport))
        past=[{'role':'user','content':'上一轮是在问县城总体规划'}, {'role':'assistant','content':'上一轮回答仅用于理解上下文'}]
        async def collect():return [event async for event in agent.run('它有哪些后续更正？',history=past,persist=False)]
        stream=asyncio.run(collect());self.assertEqual(stream[-1]['event'],'done')
        calls=[body for path,body in fixture.calls if path.endswith('/api/chat')]
        self.assertGreaterEqual(len(calls),3)
        for body in calls:self.assertTrue(any(m['content']==past[0]['content'] for m in body['messages']))
    def test_legacy_database_upgrades_without_losing_login(self):
        import hashlib,sqlite3
        from arcsync.core.store import now
        path=Path(self.tmp.name)/'old.sqlite3';salt='legacy-salt'
        with sqlite3.connect(path) as db:
            db.executescript('CREATE TABLE users(role TEXT PRIMARY KEY,name TEXT,salt TEXT,password TEXT);CREATE TABLE sessions(token TEXT PRIMARY KEY,role TEXT,created TEXT);')
            hashed=hashlib.pbkdf2_hmac('sha256',PASSWORD.encode(),salt.encode(),120000).hex()
            db.execute('INSERT INTO users VALUES(?,?,?,?)',('organizer','旧员工',salt,hashed))
            db.execute('INSERT INTO sessions VALUES(?,?,?)',('legacy-token','organizer',now()))
        with patch.dict(os.environ,{'ARCSYNC_DB':str(path)}):
            init_db()
            with TestClient(app) as client:
                result=self.login(client,'organizer');self.assertEqual(result.status_code,200)
                self.assertEqual(result.json()['name'],'旧员工');self.assertTrue(result.json()['user_id'])
            with connect() as db:self.assertIsNotNone(db.execute('SELECT * FROM session_identities WHERE token=?',('legacy-token',)).fetchone())
    def test_same_user_inflight_returns_conflict_not_global_limit(self):
        from arcsync.api.agent_routes import _active
        key='chat:'+self.user_id;_active.add(key)
        try:self.assertEqual(self.ask().status_code,409)
        finally:_active.discard(key)
    def test_separate_sqlite_storage_even_with_mysql_business_config(self):
        with patch.dict(os.environ,{'ARCSYNC_DATABASE_URL':'mysql://unused:unused@127.0.0.1/unused'}):
            with repository.chat_connect() as db:
                self.assertEqual(db.execute('PRAGMA database_list').fetchone()['file'],str((Path(self.tmp.name)/'chat.sqlite3').resolve()))

if __name__=='__main__':unittest.main()
