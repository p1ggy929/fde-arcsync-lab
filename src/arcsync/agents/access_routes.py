"""Human review surface: drafts never enter the review queue without submission."""
from html import escape
from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from ..api.security import current_user, admin_user
from ..api.routes import json_body
from ..core.store import connect
from . import access, confirmations, harness_store
from ..admin.layout import workspace_page

router=APIRouter()


@router.get('/api/access-cases')
def cases(user=Depends(current_user)):return access.view(user)


@router.get('/api/access-drafts')
def drafts(user=Depends(current_user)):return {'drafts':access.drafts(user)}


@router.post('/api/access-drafts/{identifier}/submit')
def submit(identifier:str,data:dict=Depends(json_body),user=Depends(current_user)):
    return access.submit(user,identifier)


@router.get('/api/admin/agent-tools')
def tools(user=Depends(admin_user)):
    with connect() as db:return {'items':[dict(r) for r in db.execute('SELECT * FROM agent_tool_audit ORDER BY created DESC LIMIT 100')]}


@router.get('/access-drafts',response_class=HTMLResponse)
def page(user=Depends(current_user)):
    e=lambda value:escape(str(value),quote=True)
    body=''
    for draft in access.drafts(user):
        content=draft['content']
        refs=''.join(f'<li>{e(r["document_id"])} · {e(r["version"])} · 扫描第 {r["scan_page"]} 页</li>' for r in content['archives'])
        if draft['business_id']:action=f'<a href="{e(user["home"]) }#detail/{e(draft["business_id"])}">查看已提交业务 {e(draft["business_id"])}</a>'
        elif draft['revision']!=draft['current_revision']:action='<p>事项已更新，此草稿已失效。请返回聊天重新核验并创建。</p>'
        else:action=f'<form class="submit-draft" data-id="{e(draft["id"])}" data-home="{e(user["home"])}"><button>确认内容并提交权限核验</button></form>'
        body+=f'<article class="card"><h2>{e(content["title"])}</h2><p class="draft-meta">草稿编号：{e(draft["id"])} · 事项版本 {draft["revision"]}</p><p>申请单位：{e(content["applicant"])}</p><pre>查阅目的：{e(content["purpose"])}\n利用方式：{e(content["usage"])}</pre><ul class="draft-references">{refs}</ul><p class="draft-note">候选范围未经人工卷页确认；提交后仍须权限核验、接手、人工引用确认和审核。校验凭证15分钟有效，过期须返回聊天重新校验。</p>{action}</article>'
    if not body:body='<section class="card empty-state"><img src="/favicon.svg" width="44" height="44" alt=""><h2>暂无查阅申请草稿</h2><p>前往智能查阅，说明查阅目的、档案范围及利用方式。<br>核对具体预览并确认后，草稿将在这里保存。</p><a class="btn" href="/chat-ui">开始智能查阅 →</a></section>'
    return workspace_page(user,'查阅申请草稿',body,'drafts','核对查阅事项与候选卷页，确认后提交权限核验。','ACCESS REQUEST DRAFTS')


@router.get('/api/agent/confirmations')
def list_confirmations(session_id:str|None=None,user=Depends(current_user)):
    items=confirmations.list_for(user,session_id)
    from ..chat.repository import reconcile_confirmation
    for item in items:reconcile_confirmation(user,item)
    return {'items':items}


@router.post('/api/agent/confirmations/{identifier}/resolve')
def resolve_confirmation(identifier:str,data:dict=Depends(json_body),user=Depends(current_user)):
    from ..core.service import Problem
    if set(data)!={'approved','preview_hash'} or not isinstance(data['preview_hash'],str):raise Problem('请提供approved与preview_hash')
    result=confirmations.resolve(user,identifier,data['preview_hash'],data['approved'])
    from ..chat.repository import reconcile_confirmation
    # Persisted business result survives a chat mirror failure; GET can reconcile again.
    try:
        for item in confirmations.list_for(user):
            if item['id']==identifier:reconcile_confirmation(user,item)
    except Exception:
        import logging
        logging.getLogger('arcsync').warning('Confirmation chat mirror deferred')
    return result


@router.get('/api/admin/agent-decisions')
def decision_logs(employee:str='',run_id:str='',tool:str='',start:str='',end:str='',offset:int=Query(0,ge=0),user=Depends(admin_user)):
    from ..admin.routes import dates
    start,end=dates(start,end)
    rows=harness_store.logs(employee,run_id,tool,start,end,offset)
    return {'items':rows[:50],'has_more':len(rows)>50,'next_offset':offset+50 if len(rows)>50 else None}
