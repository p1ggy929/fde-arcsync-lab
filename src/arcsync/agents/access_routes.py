"""Human review surface: drafts never enter the review queue without submission."""
from html import escape
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from ..api.security import current_user, admin_user
from ..api.routes import json_body
from ..core.store import connect
from . import access

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
        body+=f'<article class="card"><h2>{e(content["title"])}</h2><p>草稿编号：{e(draft["id"])} · 事项版本 {draft["revision"]}</p><p>申请单位：{e(content["applicant"])}</p><pre>查阅目的：{e(content["purpose"])}\n利用方式：{e(content["usage"])}</pre><ul>{refs}</ul><p>候选范围未经人工卷页确认；提交后仍须权限核验、接手、人工引用确认和审核。校验凭证15分钟有效，过期须返回聊天重新校验。</p>{action}</article>'
    if not body:body='<section class="card">暂无草稿。在智能查阅中说明目的、档案范围及利用方式，勾选允许创建草稿后发送。</section>'
    return f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>我的查阅申请草稿 · 云州档案</title><link rel="stylesheet" href="/admin/manage.css"><script src="/admin/manage.js" defer></script></head><body><aside><h2>云州档案</h2><a href="/chat-ui">智能查阅</a><a href="{e(user["home"])}">业务工作台</a></aside><main><header><h1>我的查阅申请草稿</h1><span>{e(user["name"])}</span></header><div id="notice" role="status"></div>{body}</main></body></html>'
