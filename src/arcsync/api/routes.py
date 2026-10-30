"""FastAPI routes. Synchronous SQLite work runs in the worker thread pool."""
import asyncio
import json
from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from ..core.store import connect
from ..core.service import Problem, state, mutate
from .security import current_user, admin_user, login
from .jwt_tokens import session_id
from .queries import search_documents, document_page

router = APIRouter(prefix='/api')

JSON_REQUEST = {'requestBody': {'required': True, 'content': {'application/json': {'schema': {'type': 'object'}}}}}
LOGIN_REQUEST = {'requestBody': {'required': True, 'content': {'application/json': {'schema': {
    'type': 'object', 'properties': {'username': {'type': 'string', 'example': 'organizer'},
    'password': {'type': 'string', 'format': 'password'}}, 'required': ['username','password']}}}}}


async def json_body(request: Request):
    if request.headers.get('X-ArcSync-Request') != '1':
        raise Problem('请求校验失败',403)
    origin = request.headers.get('origin')
    if origin and origin != f'{request.url.scheme}://{request.headers.get("host")}':
        raise Problem('不允许跨站请求',403)
    if request.headers.get('content-type','').split(';')[0] != 'application/json':
        raise Problem('仅接受 JSON',415)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body)>32768:
            raise Problem('请求内容过大',413)
    try:
        data=json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise Problem('请求参数格式无效')
    if not isinstance(data,dict):
        raise Problem('请求必须为 JSON 对象')
    return data

@router.get('/health')
def health():
    return {'status':'ok','framework':'FastAPI','mode':'synthetic-local','upstream_connected':False}

@router.post('/login',openapi_extra=LOGIN_REQUEST)
def sign_in(request: Request, data: dict = Depends(json_body)):
    try: old=session_id(request)
    except Problem: old=''
    user, token = login({**data,'_old_token':old})
    response=JSONResponse({**user,'access_token':token,'token_type':'bearer','expires_in':28800})
    response.set_cookie('arcsync_session',token,max_age=28800,httponly=True,samesite='strict',secure=request.url.scheme=='https')
    return response

@router.post('/logout',openapi_extra=JSON_REQUEST)
def sign_out(request: Request, data: dict = Depends(json_body), user=Depends(current_user)):
    identifier=session_id(request)
    with connect() as db:
        db.execute('DELETE FROM session_identities WHERE token=?',(identifier,))
        db.execute('DELETE FROM sessions WHERE token=?',(identifier,))
    response=JSONResponse({'ok':True})
    response.delete_cookie('arcsync_session',httponly=True,samesite='strict')
    return response

@router.get('/session')
def session(user=Depends(current_user)):
    return user

@router.get('/state')
def snapshot(user=Depends(current_user)):
    with connect() as db:
        return state(db,user)

@router.get('/admin/overview')
def overview(user=Depends(admin_user)):
    with connect() as db:
        s=state(db,user)
    return {'businesses':len(s['businesses']),'open_issues':sum(i['status']!='已处理' for i in s['issues']),
            'pending_reviews':sum(b['status'] in ('待审核','待复核','待权限核验') for b in s['businesses']),
            'paused':s['settings']['paused']=='true'}

def search_filters(q: str = Query('',max_length=200), category: str = '', year: str = '', status: str = ''):
    return {k:[v] for k,v in {'q':q,'category':category,'year':year,'status':status}.items()}

def retrieve_search(filters,user):
    with connect() as db:
        return search_documents(db,user,filters)

@router.get('/search')
def search(filters: dict=Depends(search_filters),user=Depends(current_user)):
    return retrieve_search(filters,user)

@router.get('/documents/{identifier}')
def document(identifier: str, page: int = Query(6,ge=1), user=Depends(current_user)):
    try:
        with connect() as db:
            return document_page(db,user,identifier,{'page':[str(page)]})
    except (ValueError,TypeError):
        raise Problem('请求参数无效')

@router.post('/actions/{action}',openapi_extra=JSON_REQUEST)
def action(action: str,data: dict=Depends(json_body),user=Depends(current_user)):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        result=mutate(db,user,action,data)
    return {'ok':True,**result}

@router.get('/search/stream')
async def search_stream(request: Request, filters: dict=Depends(search_filters), user=Depends(current_user)):
    # Search completes under the existing visibility checks. No connection or write
    # transaction is held while a slow client consumes the stream.
    result=await run_in_threadpool(retrieve_search,filters,user)
    def frame(event,data):
        return f'event: {event}\ndata: {json.dumps(data,ensure_ascii=False)}\n\n'
    async def chunks():
        yield frame('start',{'source':result['source'],'count':len(result['documents'])})
        for document in result['documents']:
            if await request.is_disconnected():
                return
            try:
                await run_in_threadpool(current_user,request)
            except Problem:
                yield frame('expired',{'error':'会话已失效，请重新登录'})
                return
            yield frame('document',document)
            await asyncio.sleep(0)
        yield frame('done',{'count':len(result['documents'])})
    return StreamingResponse(chunks(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no','Cache-Control':'no-store'})
