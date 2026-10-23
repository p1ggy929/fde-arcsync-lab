"""Dependency-free local application server. Run: PYTHONPATH=src python3 -m arcsync.main."""
import argparse
import hashlib
import hmac
import json
import mimetypes
import secrets
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from .core.store import ROOT, ROLES, connect, init_db, now, audit
from .core.service import Problem, mutate, state, readable_document, can_read, settings

FRONTEND=ROOT/'src'/'frontend'

class Handler(BaseHTTPRequestHandler):
    server_version='ArcSync/0.1'
    def log_message(self, fmt, *args):
        # Omit URLs/search terms and request payloads from console logs.
        pass

    def send(self, status, payload, content_type='application/json; charset=utf-8', cookie=None):
        raw=payload if isinstance(payload,bytes) else json.dumps(payload,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','same-origin')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if cookie: self.send_header('Set-Cookie',cookie)
        self.end_headers()
        self.wfile.write(raw)

    def user(self,db):
        cookie=SimpleCookie()
        try: cookie.load(self.headers.get('Cookie',''))
        except Exception: raise Problem('请重新登录',401)
        token=cookie.get('arcsync_session')
        if not token: raise Problem('请先登录',401)
        s=db.execute('SELECT s.created,u.role,u.name FROM sessions s JOIN users u ON u.role=s.role WHERE s.token=?',(token.value,)).fetchone()
        if not s or (datetime.now(timezone.utc)-datetime.fromisoformat(s['created'])).total_seconds()>28800:
            raise Problem('会话已过期，请重新登录',401)
        return {'role':s['role'],'name':s['name'],'label':ROLES[s['role']][1]}

    def do_GET(self):
        try:
            self.get()
        except Problem as e: self.send(e.status,{'error':e.message})
        except (ValueError,TypeError): self.send(400,{'error':'请求参数无效'})
        except Exception:
            self.send(500,{'error':'处理失败，请重试；现有人工流程可继续使用'})

    def get(self):
        parsed=urlparse(self.path)
        path=unquote(parsed.path)
        qs=parse_qs(parsed.query)
        if path=='/api/health': return self.send(200,{'status':'ok','mode':'synthetic-local','upstream_connected':False})
        if path.startswith('/api/'):
            with connect() as db:
                user=self.user(db)
                if path=='/api/session': return self.send(200,user)
                if path=='/api/state': return self.send(200,state(db,user))
                if path=='/api/search':
                    q=qs.get('q',[''])[0].strip()[:200]
                    category=qs.get('category',[''])[0]
                    year=qs.get('year',[''])[0]
                    status=qs.get('status',[''])[0]
                    terms=q.casefold().split()
                    docs=[]
                    for r in db.execute('SELECT * FROM documents ORDER BY year DESC'):
                        if not can_read(user,r): continue
                        d=dict(r)
                        if category and d['category']!=category: continue
                        if year and str(d['year'])!=year: continue
                        if status and d['status']!=status: continue
                        hay=' '.join(str(d[k]) for k in ('title','id','summary','category','year')).casefold()
                        if terms and not all(t in hay for t in terms): continue
                        d['match_reason']='目录与卷内摘要关键词匹配' if terms else '授权目录浏览'
                        d['recommended']=d['status']=='有效'
                        docs.append(d)
                    audit(db,user,'检索授权档案',detail={'query':q,'category':category,'count':len(docs)})
                    return self.send(200,{'documents':docs,'source':'合成目录与固定摘要；未接入 AI 或真实 OCR'})
                if path.startswith('/api/documents/'):
                    identifier=path.removeprefix('/api/documents/')
                    d=readable_document(db,user,identifier)
                    page=int(qs.get('page',['6'])[0])
                    if not 1<=page<=d['pages']: raise Problem('页码超出范围')
                    if d['status']=='待修复':
                        content=['该页日期与页码存在识别歧义。','原记录保持不变，须由人工修复后重新核验。','此资料已从自动推荐样本中排除。']
                    else:
                        content=[f'一、关于{d["title"].replace("云州县","")}的说明',
                          '根据相关资料整理，本页记录项目范围、实施安排及有关意见。各项内容应结合本卷上下文与后续更正文件进行核对。',
                          d['summary'],
                          '二、查阅与引用要求',
                          '引用时应注明档号、资料版本和纸质页码。涉及后续调整的事项，以人工核实后的适用资料为依据。']
                    audit(db,user,'查看合成卷页',detail={'document_id':d['id'],'version':d['version'],'scan_page':page})
                    return self.send(200,{'document':d,'scan_page':page,'paper_page':None if d['status']=='待修复' else d['paper_start']+page-1,
                        'paragraphs':content,'synthetic':True})
                raise Problem('接口不存在',404)
        files={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/style.css':'style.css','/favicon.svg':'favicon.svg'}
        if path not in files: raise Problem('页面不存在',404)
        f=FRONTEND/files[path]
        kind=mimetypes.guess_type(str(f))[0] or 'application/octet-stream'
        self.send(200,f.read_bytes(),kind+('; charset=utf-8' if kind.startswith('text/') or kind=='application/javascript' else ''))

    def do_POST(self):
        try:
            # Custom header plus JSON requires a same-origin request in browsers.
            if self.headers.get('X-ArcSync-Request')!='1': raise Problem('请求校验失败',403)
            origin=self.headers.get('Origin')
            if origin and origin!=f'http://{self.headers.get("Host")}': raise Problem('不允许跨站请求',403)
            if not self.headers.get('Content-Type','').startswith('application/json'): raise Problem('仅接受 JSON',415)
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=32768: raise Problem('请求内容过大或为空',413)
            data=json.loads(self.rfile.read(size))
            if not isinstance(data,dict): raise Problem('请求必须为 JSON 对象')
            path=urlparse(self.path).path
            with connect() as db:
                if path=='/api/login':
                    role=data.get('role','')
                    u=db.execute('SELECT * FROM users WHERE role=?',(role,)).fetchone()
                    candidate=hashlib.pbkdf2_hmac('sha256',str(data.get('password','')).encode(),(u['salt'] if u else 'invalid').encode(),120000).hex()
                    if not u or not hmac.compare_digest(candidate,u['password']): raise Problem('岗位或演示口令不正确',401)
                    token=secrets.token_urlsafe(40)
                    db.execute('INSERT INTO sessions VALUES(?,?,?)',(token,role,now()))
                    user={'role':role,'name':u['name'],'label':ROLES[role][1]}
                    audit(db,user,'登录演示平台')
                    return self.send(200,user,cookie=f'arcsync_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=28800')
                user=self.user(db)
                db.execute('BEGIN IMMEDIATE')
                if path=='/api/logout':
                    cookie=SimpleCookie(self.headers.get('Cookie',''))
                    db.execute('DELETE FROM sessions WHERE token=?',(cookie['arcsync_session'].value,))
                    return self.send(200,{'ok':True},cookie='arcsync_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0')
                if not path.startswith('/api/actions/'): raise Problem('接口不存在',404)
                result=mutate(db,user,path.removeprefix('/api/actions/'),data)
                self.send(200,{'ok':True,**result})
        except Problem as e: self.send(e.status,{'error':e.message})
        except (ValueError,TypeError): self.send(400,{'error':'请求参数格式无效'})
        except Exception:
            self.send(500,{'error':'操作未完成，请刷新检查后重试'})

def main():
    parser=argparse.ArgumentParser(description='ArcSync 本地合成数据演示平台')
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    init_db()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'ArcSync 已启动：http://127.0.0.1:{args.port}（合成数据演示）',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__=='__main__': main()
