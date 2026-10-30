"""Real persistence tests for the autonomous archive tools and enforced dependencies."""
import asyncio
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from arcsync.main import app
from arcsync.core.store import init_db, connect
from arcsync.core.service import Problem
from arcsync.core.accounts import create_account
from arcsync.api.security import identity
from arcsync.agents import access, confirmations
from arcsync.agents.tools import ToolBox, TOOL_SCHEMAS
from arcsync.agents.orchestrator import Orchestrator
from arcsync.data_models.knowledge import import_document
from arcsync.data_models.config import ModelSettings, Endpoint
from arcsync.admin import repository as admin

HEADERS={'X-ArcSync-Request':'1'}


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'ARCSYNC_DB':str(Path(self.tmp.name)/'db.sqlite3'),'ARCSYNC_CHAT_DB':str(Path(self.tmp.name)/'chat.sqlite3'),'ARCSYNC_DATABASE_URL':''})
        self.env.start();init_db()
        identifier=create_account('access.a','查阅甲','organizer','Archive-Test-2026!')
        other=create_account('access.b','查阅乙','organizer','Archive-Test-2026!')
        self.user=identity('organizer','查阅甲',identifier,'access.a');self.other=identity('organizer','查阅乙',other,'access.b')
        self.client=TestClient(app)
        self.client.post('/api/login',json={'username':'access.a','password':'Archive-Test-2026!'},headers=HEADERS)
        for identifier in ('TEST-A','TEST-B'):
            import_document({'role':'engineer','name':'工程师'},{'id':identifier,'version':'v1','title':identifier,'category':'城市建设','year':2026,'pages':[{'scan_page':1,'paper_page':11,'text':'规划批复原文。'}]})
        self.tools=ToolBox(self.user,object(),False,True)
    def tearDown(self):self.client.close();self.env.stop();self.tmp.cleanup()
    def call(self,name,**args):return asyncio.run(self.tools.execute(name,args))
    def case(self):
        return self.call('update_access_case',case_id=None,revision=0,title='规划批复查阅',applicant='研究室',purpose='规划沿革研究',usage='现场查阅',archives=[{'document_id':'TEST-A','version':'v1','scan_page':1}])
    def validated(self):
        case=self.case()
        self.call('read_archive_page',document_id='TEST-A',scan_page=1)
        self.call('check_archive_validity',document_id='TEST-A',version='v1')
        receipt=self.call('validate_access_request',case_id=case['id'],revision=1)
        self.assertEqual(receipt['status'],'passed',receipt)
        return case,receipt['validation_id']
    def create(self,case,token,authorized=True):
        if not authorized:return access.create(self.user,case['id'],case['revision'],token,False)
        pending=confirmations.prepare(self.user,case['id'],case['revision'],token,{})
        return confirmations.resolve(self.user,pending['id'],pending['preview_hash'],True)['draft']
    def test_dependency_checks_and_dynamic_tools(self):
        case=self.case()
        self.assertNotIn('create_access_draft',[s['function']['name'] for s in asyncio.run(self.tools.schemas())])
        receipt=self.call('validate_access_request',case_id=case['id'],revision=1)
        self.assertNotEqual(receipt['status'],'passed');self.assertIsNone(receipt['validation_id'])
        with self.assertRaises(Problem):self.create(case,'fake')
        self.call('read_archive_page',document_id='TEST-A',scan_page=1)
        self.call('check_archive_validity',document_id='TEST-A',version='v1')
        receipt=self.call('validate_access_request',case_id=case['id'],revision=1)
        self.assertIn('create_access_draft',[s['function']['name'] for s in asyncio.run(self.tools.schemas())])
        with self.assertRaises(Problem):self.create(case,receipt['validation_id'],False)
        with self.assertRaises(Problem):self.call('create_access_draft',case_id=case['id'],revision=1,validation_id=receipt['validation_id'],approved=True)
    def test_cross_employee_case_receipt_draft_and_business(self):
        case,token=self.validated();draft=self.create(case,token)
        with self.assertRaises(Problem):access.view(self.other,case['id'])
        with self.assertRaises(Problem):access.create(self.other,case['id'],1,token,True)
        with self.assertRaises(Problem):access.submit(self.other,draft['id'])
        business=access.submit(self.user,draft['id'])
        other_tools=ToolBox(self.other,object(),False)
        with self.assertRaises(Problem):asyncio.run(other_tools.execute('get_business_status',{'business_id':business['id']}))
    def test_atomic_idempotency_and_manual_submission(self):
        case,token=self.validated()
        with ThreadPoolExecutor(max_workers=4) as pool:
            drafts=list(pool.map(lambda _:self.create(case,token),range(4)))
        self.assertEqual(len({d['id'] for d in drafts}),1)
        with connect() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM businesses WHERE sample=0").fetchone()[0],0)
        response=self.client.get('/access-drafts');self.assertEqual(response.status_code,200);self.assertIn('规划批复查阅',response.text)
        response=self.client.post('/api/access-drafts/'+drafts[0]['id']+'/submit',json={},headers=HEADERS)
        self.assertEqual(response.status_code,200,response.text);bid=response.json()['id']
        self.assertEqual(access.submit(self.user,drafts[0]['id'])['id'],bid)
        with connect() as db:
            row=db.execute('SELECT * FROM businesses WHERE id=?',(bid,)).fetchone()
            self.assertEqual(row['status'],'待权限核验');self.assertEqual(row['owner'],'reviewer');self.assertEqual(json.loads(row['citations']),[])
    def test_modifications_expiry_and_revocation(self):
        for modification in ('case','version','page','level','status','expiry'):
            with self.subTest(modification=modification):
                case,token=self.validated()
                with connect() as db:
                    if modification=='case':db.execute('UPDATE access_cases SET revision=2 WHERE id=?',(case['id'],))
                    elif modification=='expiry':db.execute("UPDATE access_validations SET expires='2000-01-01' WHERE id=?",(token,))
                    elif modification=='page':db.execute("UPDATE document_chunks SET content='changed' WHERE document_id='TEST-A'")
                    else:
                        field,value={'version':('version','v2'),'level':('level','受限'),'status':('status','待修复')}[modification]
                        db.execute(f'UPDATE documents SET {field}=? WHERE id=?',(value,'TEST-A'))
                with self.assertRaises(Problem):self.create(case,token)
                with connect() as db:
                    db.execute("UPDATE documents SET version='v1',level='公开',status='有效' WHERE id='TEST-A'")
                    db.execute("UPDATE document_chunks SET content='规划批复原文。' WHERE document_id='TEST-A'")
    def test_correction_requires_read_and_invalidates_receipt(self):
        with connect() as db:db.execute("UPDATE documents SET correction='TEST-B' WHERE id='TEST-A'")
        case=self.case();self.call('read_archive_page',document_id='TEST-A',scan_page=1);self.call('check_archive_validity',document_id='TEST-A',version='v1')
        receipt=self.call('validate_access_request',case_id=case['id'],revision=1);self.assertIsNone(receipt['validation_id'])
        self.call('read_archive_page',document_id='TEST-B',scan_page=1)
        receipt=self.call('validate_access_request',case_id=case['id'],revision=1);self.assertEqual(receipt['status'],'passed')
        with connect() as db:db.execute("UPDATE documents SET summary='changed' WHERE id='TEST-B'")
        with self.assertRaises(Problem):self.create(case,receipt['validation_id'])
    def test_catalog_missing_mapping_and_cycles_block_validation(self):
        case=self.case()
        self.call('read_archive_page',document_id='TEST-A',scan_page=1);self.call('check_archive_validity',document_id='TEST-A',version='v1')
        with connect() as db:db.execute("UPDATE document_chunks SET paper_page=99 WHERE document_id='TEST-A'")
        self.assertNotEqual(self.call('validate_access_request',case_id=case['id'],revision=1)['status'],'passed')
        with connect() as db:db.execute("UPDATE documents SET correction='TEST-A' WHERE id='TEST-A'")
        with self.assertRaises(Problem):self.call('check_archive_validity',document_id='TEST-A',version='v1')
    def test_prompt_migration_preserves_custom_content(self):
        with admin.chat_connect() as db:
            admin.initialize(db)
            db.execute('UPDATE prompt_templates SET content=?',(admin.LEGACY_DEFAULT_PROMPT,))
        self.assertEqual(admin.prompt()['content'],admin.DEFAULT_PROMPT)
        template=admin.prompt();admin.save_prompt(self.user,'自定义档案提示词',template['revision'])
        self.assertEqual(admin.prompt()['content'],'自定义档案提示词')
    def test_expired_draft_can_be_revalidated_without_duplicate(self):
        case,token=self.validated();draft=self.create(case,token)
        with connect() as db:db.execute("UPDATE access_validations SET expires='2000-01-01' WHERE id=?",(token,))
        with self.assertRaises(Problem):access.submit(self.user,draft['id'])
        receipt=self.call('validate_access_request',case_id=case['id'],revision=1)
        renewed=self.create(case,receipt['validation_id']);self.assertEqual(draft['id'],renewed['id'])
        self.assertTrue(access.submit(self.user,draft['id'])['id'])
    def test_autonomous_loop_creates_only_after_tool_result(self):
        case=self.case();offered=[]
        class Model:
            async def chat(model,messages,tools=None,**kwargs):
                self.assertFalse(kwargs.get('json_mode',False));offered.append({s['function']['name'] for s in tools})
                results=[json.loads(m['content']) for m in messages if m['role']=='tool']
                actions=[('check_archive_validity',{'document_id':'TEST-A','version':'v1'}),('read_archive_page',{'document_id':'TEST-A','scan_page':1}),('validate_access_request',{'case_id':case['id'],'revision':1})]
                if len(results)<3:name,args=actions[len(results)]
                elif len(results)==3:name,args='create_access_draft',{'case_id':case['id'],'revision':1,'validation_id':results[-1]['validation_id']}
                else:return {'content':'完成'},{}
                return {'role':'assistant','content':'','tool_calls':[{'id':'call_'+str(len(results)),'function':{'name':name,'arguments':json.dumps(args)}}]},{}
            async def stream(model,messages,*args):yield {'text':'查阅草稿已创建，尚未提交 [C3]。'}
        config=ModelSettings(Endpoint('ollama','http://127.0.0.1:11434','test'),Endpoint('ollama','http://127.0.0.1:11434','test'))
        async def run():return [e async for e in Orchestrator(self.user,config,Model(),object()).run('创建草稿',persist=False,create_authorized=True)]
        result=asyncio.run(run());self.assertEqual(result[-1]['event'],'confirmation_required',result)
        self.assertNotIn('create_access_draft',offered[0]);self.assertIn('create_access_draft',offered[3])
        self.assertEqual(len(access.drafts(self.user)),0)
        pending=result[-1]['data'];confirmations.resolve(self.user,pending['id'],pending['preview_hash'],True)
        self.assertEqual(len(access.drafts(self.user)),1)
        with connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM agent_tool_audit WHERE user_id=?',(self.user['user_id'],)).fetchone()[0],4)
    def test_same_round_validate_and_create_rejected(self):
        case,token=self.validated()
        class Model:
            count=0
            async def chat(model,messages,tools=None,**kwargs):
                model.count+=1
                if model.count>1:return {'content':'请重新核验'},{}
                calls=[('validate_access_request',{'case_id':case['id'],'revision':1}),('create_access_draft',{'case_id':case['id'],'revision':1,'validation_id':token})]
                return {'role':'assistant','content':'','tool_calls':[{'id':str(i),'function':{'name':name,'arguments':json.dumps(args)}} for i,(name,args) in enumerate(calls)]},{}
            async def stream(model,messages,*args):yield {'text':'本轮请求被拒绝，尚未创建草稿。'}
        config=ModelSettings(Endpoint('ollama','http://127.0.0.1:11434','test'),Endpoint('ollama','http://127.0.0.1:11434','test'))
        async def run():return [e async for e in Orchestrator(self.user,config,Model(),object()).run('创建草稿',persist=False,create_authorized=True)]
        result=asyncio.run(run());self.assertEqual(result[-1]['event'],'done',result)
        self.assertEqual(len(access.drafts(self.user)),0)
        rejected=[e for e in result if e['event']=='tool' and e['data'].get('status')=='rejected']
        self.assertEqual(len(rejected),2)
    def test_tool_free_clarification_is_allowed(self):
        class Model:
            async def chat(model,messages,**kwargs):return {'content':'请补充目的'},{}
            async def stream(model,messages,*args):yield {'text':'请说明查阅目的及申请单位。'}
        config=ModelSettings(Endpoint('ollama','http://127.0.0.1:11434','test'),Endpoint('ollama','http://127.0.0.1:11434','test'))
        async def run():return [e async for e in Orchestrator(self.user,config,Model(),object()).run('帮我申请',persist=False)]
        result=asyncio.run(run());self.assertEqual(result[-1]['event'],'done');self.assertEqual(result[-1]['data']['sources'],[])

    def test_draft_api_does_not_expose_internal_archive_snapshot(self):
        case,token=self.validated();self.create(case,token)
        with connect() as db:db.execute("UPDATE documents SET level='受限' WHERE id='TEST-A'")
        response=self.client.get('/api/access-drafts')
        self.assertEqual(response.status_code,200)
        self.assertNotIn('规划批复原文',response.text);self.assertNotIn('snapshot',response.json()['drafts'][0])

    def test_strict_function_schemas(self):
        for schema in TOOL_SCHEMAS:
            definition=schema['function'];self.assertTrue(definition['strict'])
            params=definition['parameters'];self.assertFalse(params['additionalProperties']);self.assertEqual(set(params['properties']),set(params['required']))


if __name__=='__main__':unittest.main()
