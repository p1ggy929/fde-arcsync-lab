"""Five-call harness budget, durable human interruption and stage-5 logs."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import unittest
import test_autonomous_access as access_test_support
HEADERS=access_test_support.HEADERS
from arcsync.agents import access, confirmations
from arcsync.agents.orchestrator import Orchestrator
from arcsync.agents.repository import recover_interrupted
from arcsync.core.store import connect, init_db
from arcsync.core.service import Problem
from arcsync.data_models.config import ModelSettings, Endpoint, ModelError
from arcsync.chat import repository as chat_repository


class HarnessTests(unittest.TestCase):
    setUp=access_test_support.AccessTests.setUp
    tearDown=access_test_support.AccessTests.tearDown
    call=access_test_support.AccessTests.call
    case=access_test_support.AccessTests.case
    validated=access_test_support.AccessTests.validated

    def config(self,rounds=5):return ModelSettings(Endpoint('ollama','http://127.0.0.1:11434','test'),Endpoint('ollama','http://127.0.0.1:11434','test'),max_rounds=rounds)
    def collect(self,model,question='查阅档案',rounds=5):
        async def run():return [e async for e in Orchestrator(self.user,self.config(rounds),model,object()).run(question,persist=False)]
        return asyncio.run(run())
    def pending_model(self,case,token):
        class Model:
            count=0
            async def chat(model,messages,**kwargs):
                model.count+=1
                return {'role':'assistant','content':'','tool_calls':[{'id':'create','function':{'name':'create_access_draft','arguments':json.dumps({'case_id':case['id'],'revision':case['revision'],'validation_id':token})}}]},{}
            async def stream(model,*args):raise AssertionError('不得额外启动第六次模型调用')
        return Model()
    def pending(self):
        case,token=self.validated();model=self.pending_model(case,token)
        events=self.collect(model);self.assertEqual(events[-1]['event'],'confirmation_required',events)
        return case,token,model,events,events[-1]['data']
    def test_total_model_calls_capped_at_five_even_if_config_is_higher(self):
        class Model:
            count=0
            async def chat(model,messages,**kwargs):
                model.count+=1
                return {'role':'assistant','content':'','tool_calls':[{'id':str(model.count),'function':{'name':'get_access_case','arguments':json.dumps({'case_id':'missing-'+str(model.count)})}}]},{}
            async def stream(model,*args):raise AssertionError('不得额外调用模型')
        model=Model();events=self.collect(model,rounds=100)
        self.assertEqual(model.count,5);self.assertEqual(events[-1]['event'],'done')
        self.assertIn('5轮',events[-1]['data']['answer'])
        run_id=events[-1]['data']['run_id']
        with connect() as db:
            row=db.execute('SELECT * FROM agent_harness_runs WHERE id=?',(run_id,)).fetchone()
            self.assertEqual(row['status'],'limit_reached');self.assertEqual(row['round_count'],5)
            rows=db.execute('SELECT * FROM agent_decision_logs WHERE run_id=?',(run_id,)).fetchall()
            self.assertEqual(len([r for r in rows if r['kind']=='decision']),5)
            self.assertEqual(len([r for r in rows if r['kind']=='tool']),5)
            self.assertTrue(all(r['duration_ms']>=0 for r in rows));self.assertTrue(all(len(r['result_summary'])<=800 for r in rows))
    def test_policy_consultation_does_not_open_case_or_draft(self):
        class Model:
            count=0
            async def chat(model,messages,**kwargs):
                model.count+=1
                if model.count==1:return {'role':'assistant','tool_calls':[{'id':'rules','function':{'name':'get_access_rules','arguments':'{}'}}]},{}
                return {'content':'查阅申请须经人工权限核验及审核 [C1]。'},{}
        result=self.collect(Model(),'公开档案查阅需要哪些手续？')
        self.assertEqual(result[-1]['event'],'done',result)
        self.assertEqual(access.view(self.user)['cases'],[]);self.assertEqual(access.drafts(self.user),[])
        with connect() as db:
            names=[r[0] for r in db.execute("SELECT tool_name FROM agent_decision_logs WHERE kind='tool'")]
            self.assertEqual(names,['get_access_rules'])
    def test_interrupt_streams_exact_preview_without_creating_then_confirms(self):
        case,token,model,events,pending=self.pending()
        rendered=''.join(e['data']['text'] for e in events if e['event']=='preview_delta')
        self.assertEqual(rendered,pending['preview_text']);self.assertIn('研究室',rendered);self.assertIn('尚未创建',rendered)
        self.assertEqual(access.drafts(self.user),[]);self.assertEqual(model.count,1)
        with self.assertRaises(Problem):access.create(self.user,case['id'],1,token,pending['id'])
        response=self.client.post('/api/agent/confirmations/'+pending['id']+'/resolve',json={'approved':True,'preview_hash':pending['preview_hash']},headers=HEADERS)
        self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['status'],'completed')
        self.assertEqual(len(access.drafts(self.user)),1);self.assertEqual(model.count,1)
        with connect() as db:
            log=db.execute("SELECT * FROM agent_decision_logs WHERE run_id=? AND kind='resume' AND status='completed'",(pending['run_id'],)).fetchone()
            self.assertIsNotNone(log);self.assertEqual(log['round_no'],1);self.assertEqual(log['tool_name'],'create_access_draft')
    def test_pending_confirmation_survives_restart_and_replay_is_idempotent(self):
        _,_,model,_,pending=self.pending();init_db();recover_interrupted()
        response=self.client.get('/api/agent/confirmations');self.assertEqual(response.json()['items'][0]['status'],'waiting')
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:confirmations.resolve(self.user,pending['id'],pending['preview_hash'],True),range(4)))
        self.assertEqual(len({r['draft']['id'] for r in results}),1);self.assertEqual(len(access.drafts(self.user)),1)
        with connect() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM agent_decision_logs WHERE run_id=? AND kind='resume'",(pending['run_id'],)).fetchone()[0],1)
    def test_cancel_and_wrong_owner_cannot_create(self):
        _,_,_,_,pending=self.pending()
        with self.assertRaises(Problem):confirmations.resolve(self.other,pending['id'],pending['preview_hash'],True)
        cancelled=confirmations.resolve(self.user,pending['id'],pending['preview_hash'],False)
        self.assertEqual(cancelled['status'],'cancelled');self.assertEqual(access.drafts(self.user),[])
        with self.assertRaises(Problem):confirmations.resolve(self.user,pending['id'],pending['preview_hash'],True)
    def test_preview_hash_and_material_changes_block_resume_and_are_logged(self):
        _,_,_,_,pending=self.pending()
        with self.assertRaises(Problem):confirmations.resolve(self.user,pending['id'],'forged-hash',True)
        with connect() as db:db.execute("UPDATE document_chunks SET content='修改了正文' WHERE document_id='TEST-A'")
        response=self.client.post('/api/agent/confirmations/'+pending['id']+'/resolve',json={'approved':True,'preview_hash':pending['preview_hash']},headers=HEADERS)
        self.assertEqual(response.status_code,409);self.assertEqual(access.drafts(self.user),[])
        self.assertEqual(self.client.get('/api/agent/confirmations').json()['items'][0]['status'],'stale')
        self.assertIsNone(self.client.get('/api/agent/confirmations').json()['items'][0]['preview'])
        with connect() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM agent_decision_logs WHERE kind='resume' AND status='rejected'").fetchone()[0],2)
    def test_expired_confirmation_rejected(self):
        _,_,_,_,pending=self.pending()
        with connect() as db:db.execute("UPDATE agent_confirmations SET expires='2000-01-01' WHERE id=?",(pending['id'],))
        with self.assertRaises(Problem):confirmations.resolve(self.user,pending['id'],pending['preview_hash'],True)
        self.assertEqual(access.drafts(self.user),[])
    def test_model_failure_records_duration_and_failed_round(self):
        class Model:
            async def chat(model,*args,**kwargs):raise ModelError('模型不可用')
        events=self.collect(Model());self.assertEqual(events[-1]['event'],'error')
        with connect() as db:
            row=db.execute("SELECT * FROM agent_decision_logs WHERE kind='decision'").fetchone()
            self.assertEqual(row['status'],'failed');self.assertGreaterEqual(row['duration_ms'],0)
    def test_chat_pause_refresh_confirm_and_history_reconciliation(self):
        case,token=self.validated();model=self.pending_model(case,token)
        agent=Orchestrator(self.user,self.config(),model,object())
        with patch('arcsync.chat.routes.Orchestrator',return_value=agent):
            response=self.client.post('/chat',json={'session_id':None,'question':'创建查阅申请草稿','allow_draft':True},headers=HEADERS)
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('event: preview_delta',response.text);self.assertIn('event: confirmation_required',response.text);self.assertNotIn('event: done',response.text)
        frames=[json.loads(frame.split('data: ',1)[1]) for frame in response.text.split('\n\n') if frame.startswith('event: confirmation_required')]
        pending=frames[0]['data'];session_id=pending['session_id']
        result=self.client.get('/chat/sessions/'+session_id+'/messages').json()['data']
        self.assertEqual(result['messages'][-1]['status'],'waiting_confirmation')
        self.assertEqual(chat_repository.history(self.user,session_id,2),[])
        listed=self.client.get('/api/agent/confirmations?session_id='+session_id).json()['items'][0]
        self.assertEqual(listed['id'],pending['id'])
        response=self.client.post('/api/agent/confirmations/'+pending['id']+'/resolve',json={'approved':True,'preview_hash':pending['preview_hash']},headers=HEADERS)
        self.assertEqual(response.status_code,200,response.text)
        refreshed=self.client.get('/chat/sessions/'+session_id+'/messages').json()['data']['messages'][-1]
        self.assertEqual(refreshed['status'],'completed');self.assertIn('草稿编号',refreshed['content']);self.assertEqual(model.count,1)
    def test_decision_log_api_is_admin_only_and_filterable(self):
        _,_,_,_,pending=self.pending()
        self.assertEqual(self.client.get('/api/admin/agent-decisions').status_code,403)
        self.client.post('/api/login',json={'username':'director','password':'ArcSync2026!'},headers=HEADERS)
        response=self.client.get('/api/admin/agent-decisions',params={'run_id':pending['run_id'],'tool':'create_access_draft'})
        self.assertEqual(response.status_code,200,response.text);self.assertEqual(len(response.json()['items']),1)
        row=response.json()['items'][0];self.assertEqual(row['round_no'],1);self.assertIn('duration_ms',row);self.assertIn('result_summary',row)
        self.assertEqual(self.client.get('/api/admin/agent-decisions?run_id=missing').json()['items'],[])


if __name__=='__main__':unittest.main()
