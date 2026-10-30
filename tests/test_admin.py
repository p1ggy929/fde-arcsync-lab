"""Admin boundary, JWT revocation, atomic ingestion and live prompt tests."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from arcsync.main import app
from arcsync.core.store import init_db, connect
from arcsync.admin import repository
from arcsync.chat.repository import start_turn, finish


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'ARCSYNC_DB':str(Path(self.tmp.name)/'db.sqlite3'),'ARCSYNC_CHAT_DB':str(Path(self.tmp.name)/'chat.sqlite3'),'ARCSYNC_DATABASE_URL':'','ARCSYNC_EMBEDDING_PROVIDER':'ollama','ARCSYNC_EMBEDDING_MODEL':'test'})
        self.env.start();init_db();self.client=TestClient(app)
        self.headers={'X-ArcSync-Request':'1'}
    def tearDown(self):self.client.close();self.env.stop();self.tmp.cleanup()
    def login(self,role='director'):
        r=self.client.post('/api/login',json={'username':role,'password':os.getenv('ARCSYNC_DEMO_PASSWORD','ArcSync2026!')},headers=self.headers)
        self.assertEqual(r.status_code,200);return r.json()
    def test_boundary_jwt_and_revocation(self):
        self.assertEqual(self.client.get('/admin/manage').status_code,401)
        employee=self.login('organizer');self.assertEqual(employee['access_role'],'employee')
        self.assertEqual(len(employee['access_token'].split('.')),3)
        self.assertNotIn('jwt_secret',self.client.get('/api/state').json()['settings'])
        for path in ('/admin/manage','/api/admin/prompt','/api/admin/conversations','/api/admin/knowledge'):
            self.assertEqual(self.client.get(path).status_code,403)
        admin=self.login();token=admin['access_token'];self.client.cookies.clear()
        headers={**self.headers,'Authorization':'Bearer '+token}
        self.assertEqual(self.client.get('/api/admin/prompt',headers=headers).status_code,200)
        self.assertEqual(self.client.get('/api/session',headers={'Authorization':'Bearer '+token+'x'}).status_code,401)
        self.client.post('/api/logout',json={},headers=headers)
        self.assertEqual(self.client.get('/api/session',headers=headers).status_code,401)
    def test_prompt_conflict_and_ssr_escape(self):
        self.login();p=self.client.get('/api/admin/prompt').json()
        data={'content':'<script>alert(1)</script>新模板','revision':p['revision']}
        self.assertEqual(self.client.put('/api/admin/prompt',json=data,headers=self.headers).status_code,200)
        self.assertEqual(repository.prompt()['content'],data['content'])
        self.assertEqual(self.client.put('/api/admin/prompt',json=data,headers=self.headers).status_code,409)
        html=self.client.get('/admin/manage?section=prompt').text
        self.assertIn('&lt;script&gt;',html);self.assertNotIn('<script>alert',html)
    def test_upload_delete_and_failure_atomicity(self):
        self.login()
        with patch('arcsync.admin.knowledge.ModelClient.embed',new=AsyncMock(return_value=[[1.,0.]])):
            response=self.client.post('/api/admin/knowledge?filename=test.md',content='报销制度：差旅须真实。'.encode(),headers=self.headers)
        self.assertEqual(response.status_code,200,response.text);identifier=response.json()['id']
        with connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM vector_embeddings').fetchone()[0],1)
        self.assertEqual(len(self.client.get('/api/admin/knowledge').json()['documents']),1)
        response=self.client.request('DELETE','/api/admin/knowledge/'+identifier,json={},headers=self.headers)
        self.assertEqual(response.status_code,200)
        with connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM vector_embeddings').fetchone()[0],0)
        from arcsync.data_models.config import ModelError
        with patch('arcsync.admin.knowledge.ModelClient.embed',new=AsyncMock(side_effect=ModelError('向量失败'))):
            r=self.client.post('/api/admin/knowledge?filename=test.md',content=b'policy',headers=self.headers)
        self.assertEqual(r.status_code,503)
        self.assertEqual(self.client.get('/api/admin/knowledge').json()['documents'],[])
    def test_updated_prompt_reaches_next_answer(self):
        import asyncio
        from arcsync.agents.orchestrator import Orchestrator
        from arcsync.data_models.config import ModelSettings, Endpoint
        user=self.login()
        config=ModelSettings(Endpoint('ollama','http://127.0.0.1:11434','test'),Endpoint('ollama','http://127.0.0.1:11434','test'))
        captured=[]
        class LLM:
            async def chat(self,messages,**kwargs):
                captured.append(messages[0]['content'])
                if not any(m['role']=='tool' for m in messages):
                    return {'role':'assistant','content':'','tool_calls':[{'id':'policy','function':{'name':'get_access_rules','arguments':'{}'}}]},{}
                return {'content':'可以回答'},{}
            async def stream(self,messages,*args):
                captured.append(messages[0]['content'])
                yield {'text':'请遵循流程 [C1]。'}
        async def answer():
            return [item async for item in Orchestrator(user,config,LLM(),object()).run('查阅流程')]
        for text in ('模板第一版','模板第二版'):
            template=repository.prompt();repository.save_prompt(user,text,template['revision'])
            result=asyncio.run(answer())
            self.assertEqual(result[-1]['event'],'done')
            self.assertTrue(captured[-1].startswith(text));self.assertTrue(captured[-2].startswith(text))

    def test_audit_filters_flags(self):
        user=self.login('organizer');turn=start_turn(user,None,'能否跳过开放审核');finish(user,turn,'completed','不能跳过开放审核')
        self.login();r=self.client.get('/api/admin/conversations',params={'employee':user['user_id']}).json()
        self.assertEqual(len(r['items']),1);identifier=r['items'][0]['id']
        r=self.client.put('/api/admin/conversations/'+identifier+'/flag',json={'flagged':True,'reason':'待核实是否越权查阅'},headers=self.headers)
        self.assertEqual(r.status_code,200)
        self.assertEqual(self.client.get('/api/admin/conversations').json()['items'][0]['flagged'],1)
        self.assertEqual(self.client.get('/api/admin/conversations?start=invalid').status_code,400)
        self.assertEqual(self.client.get('/api/admin/conversations?employee=unknown').json()['items'],[])

if __name__=='__main__':unittest.main()

class PdfParsingTests(unittest.TestCase):
    def test_pdf_extract_and_reject_empty(self):
        from io import BytesIO
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        from arcsync.admin.knowledge import parse
        from arcsync.core.service import Problem
        writer=PdfWriter();page=writer.add_blank_page(width=600,height=800)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 40 700 Td (Expense policy: authentic receipts required.) Tj ET')
        page[NameObject('/Contents')]=writer._add_object(stream)
        output=BytesIO();writer.write(output)
        chunks=parse('policy.pdf',output.getvalue());self.assertIn('authentic receipts',chunks[0][1]);self.assertEqual(chunks[0][0],1)
        writer=PdfWriter();writer.add_blank_page(width=600,height=800);output=BytesIO();writer.write(output)
        with self.assertRaises(Problem):parse('scan.pdf',output.getvalue())
        with self.assertRaises(Problem):parse('broken.pdf',b'invalid')
