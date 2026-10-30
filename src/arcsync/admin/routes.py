"""Server-rendered admin workspace; all APIs enforce administrator identity."""
from datetime import datetime, timezone
from html import escape
from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool
from ..api.security import admin_user
from ..api.routes import json_body
from ..core.store import connect
from ..core.service import Problem
from ..data_models.config import ModelError
from . import repository, knowledge

router=APIRouter()


def employees():
    with connect() as db:return [dict(r) for r in db.execute('SELECT user_id,username,name FROM user_accounts ORDER BY name')]


def dates(start,end):
    values=[]
    for value in (start,end):
        if not value:values.append('');continue
        try:
            parsed=datetime.fromisoformat(value)
            if parsed.tzinfo is None:raise ValueError()
            values.append(parsed.astimezone(timezone.utc).isoformat())
        except ValueError:raise Problem('筛选时间须包含时区，例如 +08:00') from None
    if all(values) and values[0]>values[1]:raise Problem('开始时间不能晚于结束时间')
    return values


@router.get('/api/admin/knowledge')
def list_knowledge(user=Depends(admin_user)):return {'documents':knowledge.documents()}


@router.post('/api/admin/knowledge')
async def upload(request:Request,filename:str=Query(...,max_length=255),user=Depends(admin_user)):
    if request.headers.get('X-ArcSync-Request')!='1':raise Problem('请求校验失败',403)
    origin=request.headers.get('origin')
    if origin and origin!=f'{request.url.scheme}://{request.headers.get("host")}':raise Problem('不允许跨站请求',403)
    raw=bytearray()
    async for block in request.stream():
        raw.extend(block)
        if len(raw)>10*1024*1024:raise Problem('文件最多10MB',413)
    try:return await knowledge.upload(user,filename,bytes(raw))
    except ModelError as error:raise Problem(str(error),503) from None


@router.delete('/api/admin/knowledge/{identifier}')
def remove(identifier:str,data:dict=Depends(json_body),user=Depends(admin_user)):
    knowledge.delete(user,identifier);return {'ok':True}


@router.get('/api/admin/prompt')
def get_prompt(user=Depends(admin_user)):return repository.prompt()


@router.put('/api/admin/prompt')
def put_prompt(data:dict=Depends(json_body),user=Depends(admin_user)):
    if type(data.get('revision')) is not int:raise Problem('模板版本无效')
    return repository.save_prompt(user,data.get('content'),data['revision'])


@router.get('/api/admin/conversations')
def conversations(employee:str='',start:str='',end:str='',offset:int=Query(0,ge=0),user=Depends(admin_user)):
    start,end=dates(start,end)
    rows=repository.conversations(employee,start,end,offset)
    return {'items':rows[:50],'has_more':len(rows)>50,'next_offset':offset+50 if len(rows)>50 else None}


@router.put('/api/admin/conversations/{identifier}/flag')
def flag(identifier:str,data:dict=Depends(json_body),user=Depends(admin_user)):
    repository.flag(user,identifier,data.get('flagged'),data.get('reason',''));return {'ok':True}


@router.get('/admin/manage',response_class=HTMLResponse)
def workspace(section:str='knowledge',employee:str='',start:str='',end:str='',offset:int=Query(0,ge=0),user=Depends(admin_user)):
    if section not in ('knowledge','prompt','audit'):raise Problem('页面不存在',404)
    e=lambda value:escape(str(value or ''),quote=True)
    title={'knowledge':'制度知识库','prompt':'系统提示词','audit':'对话审计'}[section]
    body=''
    if section=='knowledge':
        body='''<section class="card"><h2>上传制度</h2><p>Markdown / PDF · 最大10MB · PDF须含可提取文本 · 自动切片并向量化</p><form id="upload"><input type="file" id="file" accept=".md,.pdf" required><button>上传并入库</button></form></section><section class="card"><h2>已发布制度</h2><table><thead><tr><th>文件</th><th>切片</th><th>更新时间</th><th>操作</th></tr></thead><tbody>'''
        for doc in knowledge.documents():body+=f'<tr><td>{e(doc["filename"])}</td><td>{doc["chunks"]}</td><td>{e(doc["updated"])}</td><td><button class="danger" data-delete="{e(doc["id"])}">删除</button></td></tr>'
        body+='</tbody></table><p>上传失败不会发布半成品。删除后该资料停止参与检索，关联历史引用将失效。</p></section>'
    elif section=='prompt':
        template=repository.prompt()
        body=f'''<section class="card"><h2>问答系统提示词 <span>版本 {template['revision']}</span></h2><p>保存到数据库后，下一个问答请求立即使用新版本。正在生成的回答保持当前模板。</p><form id="prompt" data-revision="{template['revision']}"><textarea id="content" maxlength="16000" required>{e(template['content'])}</textarea><button>保存并生效</button></form><p>最近修改：{e(template['updated_at'])}</p></section>'''
    else:
        options='<option value="">全部员工</option>'
        names={}
        for person in employees():
            names[person['user_id']]=person['name']+' · '+person['username']
            selected=' selected' if employee==person['user_id'] else ''
            options+=f'<option value="{e(person["user_id"])}"{selected}>{e(names[person["user_id"]])}</option>'
        body=f'''<section class="card"><form id="filters" method="get"><input type="hidden" name="section" value="audit"><label>员工<select name="employee">{options}</select></label><label>开始时间（含时区）<input name="start" placeholder="2026-10-30T00:00:00+08:00" value="{e(start)}"></label><label>结束时间（含时区）<input name="end" placeholder="2026-10-30T23:59:59+08:00" value="{e(end)}"></label><button>筛选</button></form><p>异常利用标记仅为人工复核线索，不代表违规结论。</p></section>'''
        begin,finish=dates(start,end);rows=repository.conversations(employee,begin,finish,offset)
        for item in rows[:50]:
            checked=' checked' if item['flagged'] else ''
            body+=f'''<article class="card"><h2>{e(names.get(item['user_id'],item['user_id']))} <span>{e(item['created_at'])}</span></h2><h3>员工提问</h3><pre>{e(item['question'])}</pre><h3>助手回答 · {e(item['status'])}</h3><pre>{e(item['answer']) or '暂无回答'}</pre><form class="flag" data-id="{e(item['id'])}"><label><input type="checkbox" name="flagged"{checked}>疑似越权查阅／异常利用线索</label><input name="reason" maxlength="1000" value="{e(item['reason'])}" placeholder="填写复核原因"><button>保存标记</button></form></article>'''
        if not rows:body+='<section class="card">当前筛选范围内暂无对话。</section>'
        body+='<section class="card"><h2>工具执行审计</h2><p>按当前员工与时间筛选，展示最近50条。校验通过不代表人工审核通过。</p>'
        for tool in repository.tool_events(employee,begin,finish,offset):
            body+=f'<details><summary>{e(tool["created"])} · {e(names.get(tool["user_id"],tool["user_id"]))} · {e(tool["tool"])}</summary><pre>参数：{e(tool["arguments_json"])}\n结果：{e(tool["result_json"])}</pre></details>'
        body+='</section>'
        if len(rows)>50:
            from urllib.parse import urlencode
            body+='<a href="?'+e(urlencode({'section':'audit','employee':employee,'start':start,'end':end,'offset':offset+50}))+'">下一页 →</a>'
    nav=''.join(f'<a class="{"active" if section==key else ""}" href="/admin/manage?section={key}">{name}</a>' for key,name in [('knowledge','制度知识库'),('prompt','提示词模板'),('audit','对话审计')])
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} · ArcSync 管理后台</title><link rel="stylesheet" href="/admin/manage.css"><script src="/admin/manage.js" defer></script></head><body><aside><h2>ArcSync <span>ADMIN</span></h2><p>平台管理工作台</p>{nav}<a href="/admin">业务工作台 ↗</a><a href="/">返回登录页</a></aside><main><header><div><p>WORKSPACE / ADMINISTRATION</p><h1>{title}</h1></div><div>{e(user['name'])} · 管理员</div></header><div id="notice" role="status" aria-live="polite"></div>{body}</main></body></html>'''
