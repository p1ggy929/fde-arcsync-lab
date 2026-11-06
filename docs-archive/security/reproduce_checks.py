"""Read-only source review plus isolated synthetic-db security reproductions."""
import asyncio
import json
import os
from pathlib import Path
import ssl
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()/'src'))
with tempfile.TemporaryDirectory() as folder:
 os.environ.update(ARCSYNC_DB=str(Path(folder)/'review.sqlite3'),ARCSYNC_DATABASE_URL='',ARCSYNC_DEMO_PASSWORD='ArcSync2026!',ARCSYNC_LLM_PROVIDER='disabled')
 from fastapi.testclient import TestClient
 import httpx
 from arcsync.main import app
 from arcsync.core.store import init_db,connect,now
 from arcsync.agents import repository
 from arcsync.data_models.config import ModelSettings,Endpoint
 from arcsync.api import agent_routes
 init_db();headers={'X-ArcSync-Request':'1'}
 results={}
 def login(client,role,password='ArcSync2026!'):
  return client.post('/api/login',json={'username':role,'password':password},headers=headers)
 with TestClient(app) as a,TestClient(app) as b:
  results['SEC01_default_admin_login']={role:login(a,role).status_code for role in ('director','protector','engineer')}
  os.environ['ARCSYNC_DEMO_PASSWORD']='Security-Review-Changed-Only'
  init_db()
  results['SEC01_env_change_does_not_rotate_existing_password']=login(a,'protector').status_code==200
  os.environ['ARCSYNC_DEMO_PASSWORD']='ArcSync2026!'
  statuses=[login(a,'organizer','wrong-review-password').status_code for _ in range(12)]
  results['SEC03_login_throttle']={'attempts':12,'statuses':sorted(set(statuses)),'next_valid_login':login(a,'organizer').status_code}
  with connect() as db:
   db.execute('INSERT INTO businesses(id,title,applicant,purpose,status,owner,created,updated,note) VALUES(?,?,?,?,?,?,?,?,?)',
    ('SEC-REVIEW-PRIVATE','审核岗隔离样本','测试单位','测试用途','待审核','reviewer',now(),now(),'ONLY-REVIEWER-SYNTHETIC-NOTE'))
  login(b,'representative')
  state=b.get('/api/state').json()
  results['SEC02_cross_business_read']=any(row['id']=='SEC-REVIEW-PRIVATE' and row['note']=='ONLY-REVIEWER-SYNTHETIC-NOTE' for row in state['businesses'])
  user={'role':'organizer','name':'林亦舟','label':'档案整理员'}
  identifier=repository.start_run(user,'会话A生成的隔离测试记录')
  repository.finish_run(user,identifier,'completed','隔离测试回答',[],{})
  login(b,'organizer')
  results['SEC02_same_role_sessions_share_history']=b.get('/api/agent/runs/'+identifier).status_code==200
  response=login(a,'organizer')
  cookie=response.headers['set-cookie']
  results['SEC06_plain_http_cookie']={'httponly':'httponly' in cookie.lower(),'same_site_strict':'samesite=strict' in cookie.lower(),'secure':'; secure' in cookie.lower()}
  results['non_public_file_routes']={path:a.get(path).status_code for path in ('/.env','/data/arcsync.sqlite3','/data/vectors/index.faiss','/discovery-report.md')}
  with TestClient(app,base_url='https://testserver') as secure_client:
   results['SEC06_https_cookie_secure']='; secure' in login(secure_client,'organizer').headers['set-cookie'].lower()
  results['unknown_host_accepted']=a.get('/',headers={'Host':'unconfigured.example.invalid'}).status_code
  login(a,'engineer');token=a.cookies.get('arcsync_session')
  counts={'chat':0,'embed':0}
  class FakeModels:
   def __init__(self,*args):pass
   async def chat(self,*args,**kwargs):counts['chat']+=1;await asyncio.sleep(.01);return {'content':'test'},{}
   async def embed(self,*args,**kwargs):counts['embed']+=1;await asyncio.sleep(.01);return [[1.,0.]]
  config=ModelSettings(Endpoint('cloud','https://example.invalid/v1','fixture','not-a-real-key'),Endpoint('cloud','https://example.invalid/v1','fixture-embedding','not-a-real-key'))
  async def diagnostic_calls():
   async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver',cookies={'arcsync_session':token}) as client:
    return await asyncio.gather(*[client.post('/api/admin/models/check',json={},headers=headers) for _ in range(6)])
  agent_routes._active.add('engineer')
  try:
   with patch.object(agent_routes.ModelSettings,'load',return_value=config),patch.object(agent_routes,'ModelClient',FakeModels):
    responses=asyncio.run(diagnostic_calls())
   results['SEC04_diagnostics_bypass_agent_limit']={'requests':6,'statuses':sorted({r.status_code for r in responses}),'mock_upstream_calls':counts}
  finally:agent_routes._active.discard('engineer')
  results['employee_backend_rejection']=None
  login(a,'representative');results['employee_backend_rejection']=a.get('/api/admin/overview').status_code
  results['restricted_document_rejection']=a.get('/api/documents/YZ-R-1991-002?page=1').status_code
 import pymysql
 raw=pymysql.connect(host='127.0.0.1',user='review-only',defer_connect=True)
 results['SEC05_mysql_default_tls']={'allows_fallback':not raw._ssl_required,'verifies_certificate':raw.ctx.verify_mode!=ssl.CERT_NONE,'verifies_hostname':raw.ctx.check_hostname}
 print(json.dumps(results,ensure_ascii=False,indent=2))
 Path('/tmp/arcsync-security-evidence.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
