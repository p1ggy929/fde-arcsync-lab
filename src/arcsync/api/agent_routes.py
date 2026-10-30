"""Authenticated agent streaming, knowledge ingestion and model diagnostics."""
import asyncio
from contextlib import aclosing
import json
import time
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from ..core.store import connect, audit
from ..core.service import Problem, require, text, check_active
from ..data_models.config import ModelSettings, ModelError
from ..data_models.providers import ModelClient
from ..data_models.knowledge import import_document
from ..agents.orchestrator import Orchestrator
from ..agents import repository
from .security import current_user
from .routes import json_body, JSON_REQUEST

router=APIRouter(prefix='/api')
# Bounded local concurrency; release even when a browser disconnects.
_active=set()

@router.get('/models/status')
def model_status(user=Depends(current_user)):
    return ModelSettings.load().public()

@router.post('/admin/models/check',openapi_extra=JSON_REQUEST)
async def check_models(data:dict=Depends(json_body),user=Depends(current_user)):
    require(user,('engineer','protector'))
    config=ModelSettings.load()
    try:
        config.validate();started=time.monotonic()
        message,_=await ModelClient(config.llm).chat([{'role':'user','content':'请回复：连接正常。'}])
        vectors=await ModelClient(config.embedding).embed(['ArcSync connection test'])
    except ModelError as error:raise Problem(str(error),503) from None
    def record():
        with connect() as db:audit(db,user,'检查模型连接',detail={'provider':config.llm.provider,'model':config.llm.model})
    await run_in_threadpool(record)
    return {'ok':True,'provider':config.llm.provider,'model':config.llm.model,'embedding_dimension':len(vectors[0]),
            'latency_ms':round((time.monotonic()-started)*1000),'notice':'连通性通过；工具调用支持与回答质量须在实际任务中验证'}

@router.post('/admin/knowledge/documents',openapi_extra=JSON_REQUEST)
def ingest(data:dict=Depends(json_body),user=Depends(current_user)):
    return import_document(user,data)

@router.get('/agent/runs')
def runs(user=Depends(current_user)):
    return {'runs':repository.history(user)}

@router.get('/agent/runs/{identifier}')
def run_detail(identifier:str,user=Depends(current_user)):
    return repository.detail(user,identifier)

@router.post('/agent/chat/stream',openapi_extra=JSON_REQUEST)
async def chat(request:Request,data:dict=Depends(json_body),user=Depends(current_user)):
    question=text(data,'question',2000)
    if type(data.get('allow_draft',False)) is not bool:raise Problem('allow_draft须为布尔值')
    try:agent=Orchestrator(user)
    except ModelError as error:raise Problem(str(error),503) from None
    def active():
        with connect() as db:check_active(db)
    await run_in_threadpool(active)
    if user['role'] in _active or len(_active)>=4:
        raise Problem('智能查阅繁忙，请等待当前任务结束后重试',429)
    _active.add(user['role'])
    async def guard():
        if await request.is_disconnected():raise asyncio.CancelledError()
        latest=await run_in_threadpool(current_user,request)
        if latest['role']!=user['role']:raise Problem('账号已变化，请重新提问',401)
    async def events():
        try:
            options={'create_authorized':True} if data.get('allow_draft') else {}
            async with aclosing(agent.run(question,guard,**options)) as stream:
                async for event in stream:
                    yield f'event: {event["event"]}\ndata: {json.dumps(event["data"],ensure_ascii=False)}\n\n'
        except Exception:
            yield 'event: error\ndata: '+json.dumps({'error':'智能查阅服务暂不可用，请重试','discard_draft':True},ensure_ascii=False)+'\n\n'
        finally:_active.discard(user['role'])
    return StreamingResponse(events(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no','Cache-Control':'no-store'})
