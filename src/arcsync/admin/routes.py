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
from . import repository, knowledge, evaluations
from .layout import workspace_page

router=APIRouter()


@router.get('/api/admin/evaluations')
def evaluation_cases(user=Depends(admin_user)):
    return {'code':0,'message':'读取评测题库成功','data':evaluations.dataset()}


@router.get('/api/admin/evaluations/download')
def download_evaluations(user=Depends(admin_user)):
    # 下载保持用户约定的JSON数组格式，权限与管理后台一致。
    from fastapi.responses import FileResponse
    return FileResponse(evaluations.DATASET_PATH,media_type='application/json',filename='yunzhou-archives.json')


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
    if section not in ('knowledge','prompt','audit','evaluation','usage'):raise Problem('页面不存在',404)
    e=lambda value:escape(str(value or ''),quote=True)
    title={'knowledge':'制度知识库','prompt':'系统提示词','audit':'对话审计','evaluation':'项目评测','usage':'模型用量'}[section]
    body=''
    if section=='knowledge':
        body='''<section class="card"><h2>上传制度</h2><p>Markdown / PDF · 最大10MB · PDF须含可提取文本 · 自动切片并向量化</p><form id="upload"><input type="file" id="file" accept=".md,.pdf" required><button>上传并入库</button></form></section><section class="card"><h2>已发布制度</h2><table><thead><tr><th>文件</th><th>切片</th><th>更新时间</th><th>操作</th></tr></thead><tbody>'''
        for doc in knowledge.documents():body+=f'<tr><td>{e(doc["filename"])}</td><td>{doc["chunks"]}</td><td>{e(doc["updated"])}</td><td><button class="danger" data-delete="{e(doc["id"])}">删除</button></td></tr>'
        body+='</tbody></table><p>上传失败不会发布半成品。删除后该资料停止参与检索，关联历史引用将失效。</p></section>'
    elif section=='prompt':
        template=repository.prompt()
        body=f'''<section class="card"><h2>问答系统提示词 <span>版本 {template['revision']}</span></h2><p>保存到数据库后，下一个问答请求立即使用新版本。正在生成的回答保持当前模板。</p><form id="prompt" data-revision="{template['revision']}"><textarea id="content" maxlength="16000" required>{e(template['content'])}</textarea><button>保存并生效</button></form><p>最近修改：{e(template['updated_at'])}</p></section>'''
    elif section=='usage':
        from ..core.service import require
        require(user,('director','protector','engineer'))
        from ..production import enabled
        if not enabled():body='<section class="card"><h2>开发模式</h2><p>生产模式启用共享配额与用量台账；开发模式不计入生产预算。</p></section>'
        else:
            from ..production.controls import usage_summary
            try:summary=usage_summary()
            except Exception:raise Problem('用量服务暂不可用',503) from None
            body=f'<section class="card"><h2>今日模型预算占用（UTC）</h2><p>预留Token：{summary["reserved_tokens"]} · 预留美元：{summary["reserved_micro_usd"]/1000000:.6f}</p><p>{e(summary["notice"])}</p></section>'
            for item in summary['items']:
                body+=f'<article class="card"><h3>{e(item["model"])} · {e(item["kind"])}</h3><p>用户：{e(item["user_id"])} · 状态：{e(item["status"])} · 耗时：{e(item.get("duration_ms","处理中"))} ms</p><pre>{e(item.get("reported_usage",{}))}</pre></article>'
    elif section=='evaluation':
        dataset=evaluations.dataset()
        body=f'''<section class="card"><h2>云州档案评测题库 · {dataset['total']} 条</h2><p>制度类10条（含3条无答案陷阱题）、工具调用类10条、违规意图类5条、多轮上下文类5条。</p><p>用于人工或后续自动评测，不代表已经执行或通过。评测预期不进入档案检索知识库。</p><a href="/api/admin/evaluations/download">下载 JSON 题库</a></section>'''
        for index,item in enumerate(dataset['items'],1):
            sequence=' → '.join(item['expected_tools']) or '无'
            body+=f'''<article class="card"><h2>{index:02d} · {e(item['category'])}</h2><h3>问题／多轮脚本</h3><pre>{e(item['question'])}</pre><h3>期望工具顺序</h3><pre>{e(sequence)}</pre><h3>预期行为及前置条件</h3><pre>{e(item['expected_behavior'])}</pre></article>'''
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
        body+='</section><section class="card"><h2>Harness 决策日志</h2><p>每次最多5轮，记录工具参数、耗时、状态和结果摘要；不记录隐藏思考。</p>'
        from ..agents.harness_store import logs
        for entry in logs(employee=employee,start_at=begin,end_at=finish,offset=offset)[:50]:
            body+=f'<details><summary>第 {entry["round_no"]} 轮 · {e(entry["tool_name"])} · {entry["duration_ms"]} ms · {e(entry["status"])} · {e(entry["created"])}</summary><pre>运行：{e(entry["run_id"])}\n参数：{e(entry["arguments_json"])}\n摘要：{e(entry["result_summary"])}</pre></details>'
        body+='</section>'
        if len(rows)>50:
            from urllib.parse import urlencode
            body+='<a href="?'+e(urlencode({'section':'audit','employee':employee,'start':start,'end':end,'offset':offset+50}))+'">下一页 →</a>'
    tabs='<nav class="management-tabs" aria-label="管理功能">'+''.join(f'<a class="{"active" if section==key else ""}" href="/admin/manage?section={key}"'+(' aria-current="page"' if section==key else '')+f'>{name}</a>' for key,name in [('knowledge','制度知识库'),('prompt','提示词模板'),('audit','对话审计'),('evaluation','项目评测'),('usage','模型用量')])+'</nav>'
    return workspace_page(user,title,body,'manage','统一维护查阅依据、问答规则与服务记录，让每一次辅助都有据可查。','KNOWLEDGE & GOVERNANCE',tabs)
