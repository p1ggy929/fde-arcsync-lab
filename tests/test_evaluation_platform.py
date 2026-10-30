"""评测隔离、评分、门禁、并发队列与独立人工复核。测试不调用付费模型。"""
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from arcsync.main import app
from arcsync.core.store import init_db, connect
from arcsync.core.service import Problem
from arcsync.evaluation import repository, scoring, runner
from arcsync.evaluation.specs import suite
from arcsync.evaluation.profiles import select


class EvaluationPlatformTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'ARCSYNC_ENV':'test','ARCSYNC_DB':self.tmp.name+'/real.sqlite3',
            'ARCSYNC_CHAT_DB':self.tmp.name+'/chat.sqlite3','ARCSYNC_DATABASE_URL':'',
            'ARCSYNC_LLM_PROVIDER':'ollama','ARCSYNC_LLM_BASE_URL':'http://127.0.0.1:11434','ARCSYNC_LLM_MODEL':'test',
            'ARCSYNC_EMBEDDING_PROVIDER':'ollama','ARCSYNC_EMBEDDING_BASE_URL':'http://127.0.0.1:11434','ARCSYNC_EMBEDDING_MODEL':'test','ARCSYNC_EVALUATION_PROFILES':''})
        self.env.start();init_db();self.owner={'user_id':'owner'};self.reviewer={'user_id':'reviewer'}
    def tearDown(self):self.env.stop();self.tmp.cleanup()
    def create(self,ids=None):return repository.create(self.owner,'current',ids or [1])
    def result(self,case_id=1,fail=False):
        return {'case_id':case_id,'status':'completed','duration_ms':12,'usage':{'input_tokens':3,'output_tokens':2},'turns':[],
                'score':{'auto_status':'failed' if fail else 'needs_review','checks':[]}}
    def finish(self,job,result=None):
        identifier=repository.claim('worker');self.assertEqual(identifier,job['id'])
        repository.save_result(identifier,'worker',result or self.result())
        repository.finish(identifier,'worker','completed')
        return repository.detail(identifier)
    def test_suite_turns_profiles_snapshot_and_no_secrets(self):
        spec=suite();self.assertEqual(len(spec['cases']),30)
        self.assertEqual(len(spec['cases'][29]['turns']),3)
        self.assertEqual(len(spec['cases'][25]['turns']),2)
        job=self.create([26]);self.assertEqual(job['snapshot']['case_ids'],[26])
        self.assertEqual(job['snapshot']['suite']['hash'],spec['hash'])
        self.assertNotIn('api_key',json.dumps(job))
        with self.assertRaises(Problem):self.create()
    def test_queue_claim_stop_and_restart(self):
        job=self.create();self.assertEqual(repository.claim('one'),job['id']);self.assertIsNone(repository.claim('two'))
        with repository.db() as db:db.execute('UPDATE evaluation_jobs SET heartbeat=? WHERE id=?',(time.time()-100,job['id']))
        self.assertIsNone(repository.claim('two'))
        self.assertEqual(repository.detail(job['id'])['status'],'interrupted')
        new=self.create();repository.stop(self.owner,new['id']);self.assertIsNone(repository.claim('three'))
        self.assertEqual(repository.detail(new['id'])['status'],'cancelled')
    def test_review_separation_revision_and_immutable_failure(self):
        job=self.create();self.finish(job)
        data={'decision':'passed','reason':'人工核对依据与流程正确','revision':1}
        with self.assertRaises(Problem):repository.review(self.owner,job['id'],1,data)
        after=repository.review(self.reviewer,job['id'],1,data)
        self.assertEqual(after['summary']['passed'],1)
        self.assertEqual(after['gate']['status'],'blocked')
        with self.assertRaises(Problem):repository.review(self.reviewer,job['id'],1,data)
        bad=self.create();self.finish(bad,self.result(fail=True))
        with self.assertRaises(Problem):repository.review(self.reviewer,bad['id'],1,data)
    def test_gate_all_cases_and_critical_failure(self):
        job={'status':'completed'}
        rows=[{'case_id':i,'review':'passed','result':self.result(i)} for i in range(1,31)]
        self.assertEqual(scoring.gate(job,rows)['status'],'ready_for_release_review')
        rows[20]['result']['score']['auto_status']='failed'
        self.assertEqual(scoring.gate(job,rows)['status'],'blocked')
        self.assertIn(21,scoring.gate(job,rows)['failed_cases'])
        self.assertEqual(scoring.gate(job,rows[:10])['status'],'blocked')
    def test_scoring_tools_citations_and_unsafe_write(self):
        case=suite()['cases'][10]
        result={'status':'completed','turns':[{'answer':'事实[C7]','sources':[],'tools':[{'name':'search_archives'}]}],'draft_count':1}
        scored=scoring.score(case,result);self.assertEqual(scored['auto_status'],'failed')
        self.assertTrue(any(c['name']=='no_unconfirmed_write' and not c['passed'] for c in scored['checks']))
        result['draft_count']=0;result['turns'][0]['answer']='目录[C1]';result['turns'][0]['sources']=[{'label':'C1'}]
        self.assertEqual(scoring.score(case,result)['auto_status'],'needs_review')
    def test_compare_incompatible_suite(self):
        a=self.create();self.finish(a)
        b=self.create();self.finish(b)
        self.assertTrue(repository.compare(a['id'],b['id'])['comparable'])
        with repository.db() as db:
            snapshot=b['snapshot'];snapshot['suite']['hash']='changed'
            db.execute('UPDATE evaluation_jobs SET snapshot=? WHERE id=?',(json.dumps(snapshot),b['id']))
        self.assertFalse(repository.compare(a['id'],b['id'])['comparable'])
    def test_api_authorization_page_and_report(self):
        # 不启动队列生命周期，接口测试不会触发真实模型。
        client=TestClient(app);headers={'X-ArcSync-Request':'1'}
        self.assertEqual(client.get('/api/admin/evaluation/jobs').status_code,401)
        def login(role):
            client.post('/api/login',json={'username':role,'password':os.getenv('ARCSYNC_DEMO_PASSWORD','ArcSync2026!')},headers=headers)
        login('organizer');self.assertEqual(client.get('/api/admin/evaluation/jobs').status_code,403)
        login('reviewer');self.assertEqual(client.post('/api/admin/evaluation/jobs',json={},headers=headers).status_code,403)
        login('director');response=client.post('/api/admin/evaluation/jobs',json={'profile':'current','case_ids':[1]},headers=headers)
        self.assertEqual(response.status_code,202,response.text);identifier=response.json()['data']['id']
        self.assertIn('evaluation-start',client.get('/admin/manage?section=evaluation').text)
        self.assertEqual(client.get('/api/admin/evaluation/jobs/'+identifier+'/report').status_code,200)
        self.assertEqual(client.post('/api/admin/evaluation/jobs',json={'model_url':'https://evil'},headers=headers).status_code,400)
        client.close()
    def test_disabled_model_no_phantom_job(self):
        with patch.dict(os.environ,{'ARCSYNC_LLM_PROVIDER':'disabled'}):
            from arcsync.data_models.config import ModelError
            with self.assertRaises(ModelError):self.create()
        self.assertEqual(repository.listed(),[])
    def test_queued_stop_does_not_spawn(self):
        job=self.create();repository.stop(self.owner,job['id']);self.assertIsNone(repository.claim('x'))
    def test_case_sandbox_isolated_and_multiturn_history(self):
        from arcsync.evaluation.worker import execute_case
        snapshot=self.create([26])['snapshot'];seen=[]
        class Engine:
            def __init__(self,user,config=None):self.user=user
            async def run(self,question,history=None,**kwargs):
                seen.append((os.environ['ARCSYNC_DB'],list(history or [])))
                if len(seen)==1:
                    with connect() as db:db.execute("UPDATE documents SET title='仅沙箱修改' WHERE id='YZ-CJ-1986-001'")
                yield {'event':'done','data':{'answer':'评测回答','sources':[],'usage':{'input_tokens':1}}}
        original=os.environ.copy()
        try:result=asyncio.run(execute_case(snapshot['suite']['cases'][25],snapshot,select('current'),Engine))
        finally:os.environ.clear();os.environ.update(original)
        self.assertEqual(result['status'],'completed');self.assertEqual(len(seen),2);self.assertEqual(len(seen[1][1]),2)
        self.assertNotEqual(seen[0][0],original['ARCSYNC_DB'])
        self.assertFalse(Path(seen[0][0]).exists())
        with connect() as db:self.assertNotEqual(db.execute("SELECT title FROM documents WHERE id='YZ-CJ-1986-001'").fetchone()[0],'仅沙箱修改')
    def test_real_subprocess_protocol_and_stop(self):
        # 仅测试注入脚本替换模型决策，不向生产worker开放模拟模型开关。
        from arcsync.core.runtime import BUILD_ID
        script='''import asyncio,json,sys
from arcsync.evaluation import worker
from arcsync.agents.orchestrator import Orchestrator
class Model:
 async def chat(self,messages,**kwargs):
  if not any(m.get('role')=='tool' for m in messages):
   return {'role':'assistant','content':'','tool_calls':[{'id':'rule','function':{'name':'get_access_rules','arguments':'{}'}}]},{'input_tokens':2,'output_tokens':1}
  return {'role':'assistant','content':'需人工审核 [C1]。'},{'input_tokens':2,'output_tokens':1}
class Engine(Orchestrator):
 def __init__(self,user,config=None):super().__init__(user,config,Model(),object())
original=worker.execute_case
async def run(case,snapshot,config):return await original(case,snapshot,config,Engine)
worker.execute_case=run
asyncio.run(worker.main(json.loads(sys.stdin.read())))
'''
        async def run_completed():
            with connect() as db:self.owner={'user_id':db.execute("SELECT user_id FROM user_accounts WHERE username='director'").fetchone()[0]}
            job=self.create();identifier=repository.claim('integration')
            actual=asyncio.create_subprocess_exec
            async def spawn(*args,**kwargs):return await actual(args[0],'-c',script,**kwargs)
            with patch('arcsync.evaluation.runner.asyncio.create_subprocess_exec',side_effect=spawn):
                await runner.execute(identifier,'integration')
            result=repository.detail(identifier)
            self.assertEqual(result['status'],'completed',result.get('error'))
            self.assertEqual(result['results'][0]['result']['turns'][0]['tools'][0]['name'],'get_access_rules')
            self.assertEqual(result['results'][0]['result']['score']['auto_status'],'needs_review')
            self.assertEqual(result['gate']['status'],'blocked')
            job=self.create();identifier=repository.claim('stopper')
            async def sleeper(*args,**kwargs):return await actual(args[0],'-c','import time; time.sleep(60)',**kwargs)
            with patch('arcsync.evaluation.runner.asyncio.create_subprocess_exec',side_effect=sleeper):
                task=asyncio.create_task(runner.execute(identifier,'stopper'))
                await asyncio.sleep(.2);repository.stop(self.owner,identifier)
                await asyncio.wait_for(task,8)
            self.assertEqual(repository.detail(identifier)['status'],'cancelled')
        asyncio.run(run_completed())
    def test_same_round_create_and_cross_turn_receipt_rejected(self):
        case=suite()['cases'][18]
        tool=lambda name,status,round_no:{'name':name,'round_no':round_no,'result':{'status':status}}
        result={'status':'completed','turns':[{'answer':'预览','sources':[],'status':'waiting_confirmation','tools':[
            tool('validate_access_request','passed',2),tool('create_access_draft','waiting_confirmation',2)]}]}
        self.assertEqual(scoring.score(case,result)['auto_status'],'failed')
    def test_review_audit_and_missing_results_never_pass(self):
        job=self.create();self.finish(job)
        reviewed=repository.review(self.reviewer,job['id'],1,{'decision':'failed','revision':1,'reason':'人工核对发现结论不符'})
        self.assertEqual(reviewed['gate']['status'],'blocked')
        with repository.db() as db:
            audit=db.execute('SELECT * FROM evaluation_reviews WHERE job_id=?',(job['id'],)).fetchone()
            self.assertEqual(audit['actor'],'reviewer')
    def test_release_gate_version_prompt_and_model(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('gate_script',Path(__file__).parents[1]/'scripts/check_evaluation_gate.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        job=self.create();job['gate']={'status':'ready_for_release_review'};snapshot=job['snapshot']
        self.assertTrue(module.validate(job,snapshot['code_version'],snapshot['prompt'],snapshot['model'],snapshot['suite']['hash']))
        with self.assertRaises(ValueError):module.validate(job,'other-code',snapshot['prompt'],snapshot['model'],snapshot['suite']['hash'])
        with self.assertRaises(ValueError):module.validate(job,snapshot['code_version'],{'content':'changed'},snapshot['model'],snapshot['suite']['hash'])
        with self.assertRaises(ValueError):module.validate(job,snapshot['code_version'],snapshot['prompt'],{},snapshot['suite']['hash'])
    def test_complete_case_setup_real_validation_preconditions(self):
        from arcsync.evaluation.worker import prepare
        from arcsync.agents.tools import ToolBox
        job=self.create([18]);snapshot=job['snapshot'];original=os.environ.copy()
        async def run():
            tools=ToolBox(user,object(),False)
            saved=await tools.execute('get_access_case',{'case_id':None})
            case=saved['cases'][0]
            await tools.execute('read_archive_page',{'document_id':'YZ-CJ-1986-001','scan_page':6})
            await tools.execute('read_archive_page',{'document_id':'YZ-CJ-1988-003','scan_page':1})
            await tools.execute('check_archive_validity',{'document_id':'YZ-CJ-1986-001','version':'v2.0'})
            result=await tools.execute('validate_access_request',{'case_id':case['id'],'revision':case['revision']})
            self.assertEqual(result['status'],'passed',result)
        try:
            with tempfile.TemporaryDirectory() as folder:
                user=prepare(folder,snapshot['suite']['cases'][17],snapshot)
                asyncio.run(run())
        finally:os.environ.clear();os.environ.update(original)
    def test_revoked_owner_stops_running_job(self):
        with connect() as db:self.owner={'user_id':db.execute("SELECT user_id FROM user_accounts WHERE username='director'").fetchone()[0]}
        job=self.create();repository.claim('w')
        self.assertFalse(repository.pulse(job['id'],'w'))
        with connect() as db:db.execute('UPDATE user_accounts SET enabled=0 WHERE user_id=?',(self.owner['user_id'],))
        self.assertTrue(repository.pulse(job['id'],'w'))
