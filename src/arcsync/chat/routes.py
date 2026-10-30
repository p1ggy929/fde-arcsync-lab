"""多轮聊天HTTP接口：握手前返回JSON错误，握手后以SSE错误事件结束。"""
import asyncio
from contextlib import aclosing
import json
import time
import uuid
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from ..api.routes import json_body
from ..api.security import current_user
from ..api.agent_routes import _active
from ..agents.orchestrator import Orchestrator
from ..core.service import Problem, check_active
from ..core.store import connect
from ..data_models.config import ModelError
from . import repository

router=APIRouter()
TASK_TIMEOUT=180
REQUEST_SCHEMA={
    'requestBody':{
        'required':True,
        'content':{'application/json':{'schema':{
            'type':'object','additionalProperties':False,'required':['session_id','question'],
            'properties':{'session_id':{'type':['string','null']},
                          'question':{'type':'string','minLength':1,'maxLength':2000},'allow_draft':{'type':'boolean','default':False}}
        }}}
    }
}


def payload(code,message,data=None):
    return {'code':code,'message':message,'data':data}


def event(name,message,data=None,code=0):
    # 每个事件由空行分隔；正文通过JSON转义，不拼接未经处理的换行。
    return 'event: '+name+'\ndata: '+json.dumps(payload(code,message,data),ensure_ascii=False)+'\n\n'


def canonical_session(value):
    if value is None:return None
    if not isinstance(value,str):raise Problem('会话ID格式无效')
    try:return uuid.UUID(value).hex
    except (ValueError,AttributeError):raise Problem('会话ID格式无效') from None


def citations(sources):
    """只回传文件名、版本与片段，不把完整模型上下文发送给浏览器。"""
    return [{'id':source['label'],'document_id':source.get('document_id'),
             'filename':source.get('filename') or source['title'],'version':source.get('version'),
             'section':source.get('section'),'page':source.get('paper_page'),
             'snippet':source['content'][:500]} for source in sources]


@router.get('/chat/sessions')
def list_sessions(limit:int=Query(20,ge=1,le=50),offset:int=Query(0,ge=0),user=Depends(current_user)):
    return payload(0,'会话列表读取成功',repository.sessions(user,limit,offset))


@router.get('/chat/sessions/{session_id}/messages')
def list_messages(session_id:str,limit:int=Query(20,ge=1,le=50),before_turn:int|None=Query(None,ge=1),user=Depends(current_user)):
    identifier=canonical_session(session_id)
    return payload(0,'会话消息读取成功',repository.messages(user,identifier,limit,before_turn))


@router.post('/chat',openapi_extra=REQUEST_SCHEMA)
async def chat(request:Request,data:dict=Depends(json_body),user=Depends(current_user)):
    # 身份从Cookie取得，拒绝客户端伪造user_id、历史消息或模型地址。
    if not {'session_id','question'}<=set(data) or set(data)-{'session_id','question','allow_draft'}:raise Problem('请仅提供session_id、question和可选allow_draft字段')
    if type(data.get('allow_draft',False)) is not bool:raise Problem('allow_draft须为布尔值')
    question=data['question']
    if not isinstance(question,str) or not 1<=len(question.strip())<=2000:raise Problem('问题不能为空且不得超过2000字')
    question=question.strip();session_id=canonical_session(data['session_id'])
    await run_in_threadpool(repository.inspect_session,user,session_id)
    try:agent=Orchestrator(user)
    except ModelError as error:raise Problem(str(error),503) from None
    def active():
        with connect() as db:check_active(db)
    await run_in_threadpool(active)
    user_key='chat:'+repository.identity(user)
    if user_key in _active:raise Problem('当前会话或账号正在生成回答，请稍后再试',409)
    if len(_active)>=4:raise Problem('智能查阅繁忙，请稍后再试',429)
    _active.add(user_key);turn=None
    try:
        turn=await run_in_threadpool(repository.start_turn,user,session_id,question,TASK_TIMEOUT)
        history=await run_in_threadpool(repository.history,user,turn['session_id'],turn['turn_no'])
    except BaseException:
        _active.discard(user_key)
        if turn:
            await run_in_threadpool(repository.finish,user,turn,'failed','',None,None,50001,'请求未完成，请重新提问')
        raise

    async def guard():
        if await request.is_disconnected():raise asyncio.CancelledError()
        latest=await run_in_threadpool(current_user,request)
        if latest['user_id']!=user['user_id']:raise Problem('登录身份已变化，请重新登录',401)
        await run_in_threadpool(repository.check_turn,user,turn['session_id'],turn['message_id'])

    async def stream():
        # 队列有界，浏览器接收变慢时向上游传播背压，不无限缓存token。
        queue=asyncio.Queue(maxsize=32);terminal=False
        async def produce():
            try:
                options={'create_authorized':True} if data.get('allow_draft') else {}
                async with aclosing(agent.run(question,guard,history=history,persist=False,**options)) as source:
                    async for item in source:await queue.put(item)
            except asyncio.CancelledError:raise
            except Exception:await queue.put({'event':'error','data':{'error':'模型服务暂不可用，请稍后重试'}})
            finally:
                # 被取消时无需再向已经无人消费的队列写结束标记。
                if not asyncio.current_task().cancelling():await queue.put(None)
        worker=asyncio.create_task(produce());last_heartbeat=time.monotonic()
        try:
            yield event('session','会话已就绪',{'session_id':turn['session_id'],'message_id':turn['message_id']})
            yield event('status','正在检索制度…',{'stage':'retrieving'})
            # 总时限覆盖整个任务，不只限制单次模型读取等待。
            async with asyncio.timeout(TASK_TIMEOUT):
                while True:
                    await guard()
                    try:item=await asyncio.wait_for(queue.get(),timeout=1)
                    except TimeoutError:
                        if time.monotonic()-last_heartbeat>=10:
                            yield ': heartbeat\n\n';last_heartbeat=time.monotonic()
                        continue
                    if item is None:raise ModelError('模型连接中断，请重新提问')
                    kind=item['event'];body=item['data']
                    if kind=='step':
                        stage=body.get('stage')
                        if stage=='deciding':yield event('status','正在判断下一步查阅操作…',{'stage':'deciding'})
                        if stage=='generate':yield event('status','正在生成回答…',{'stage':'generating'})
                        elif stage=='retrieval':yield event('status','正在检索制度…',{'stage':'retrieving'})
                    elif kind=='tool':yield event('status','正在核对制度条文…',{'stage':'retrieving'})
                    elif kind=='token':yield event('delta','回答生成中',{'text':body['text']})
                    elif kind=='error':raise ModelError(body.get('error','回答生成失败，请稍后重试'))
                    elif kind=='done':
                        sources=body['sources'];items=citations(sources)
                        await run_in_threadpool(repository._validate_sources,user,sources)
                        await run_in_threadpool(repository.finish,user,turn,'completed',body['answer'],sources,items)
                        terminal=True
                        yield event('citations','引用来源已就绪',{'items':items})
                        yield event('done','回答已完成并保存',{'session_id':turn['session_id'],'message_id':turn['message_id'],
                                                           'status':'completed','answer':body['answer']})
                        return
        except (asyncio.CancelledError,GeneratorExit):raise
        except Exception as error:
            if isinstance(error,Problem):code=error.status*100+1;message=error.message
            elif isinstance(error,TimeoutError):code=50302;message='回答生成超时，请缩小问题范围后重试'
            elif isinstance(error,ModelError):
                message=str(error);code=40402 if '未检索到' in message else 50302
            else:code=50001;message='服务处理失败，请稍后重试'
            await run_in_threadpool(repository.finish,user,turn,'failed','',None,None,code,message)
            terminal=True
            yield event('error',message,{'session_id':turn['session_id'],'message_id':turn['message_id'],'discard_draft':True},code)
        finally:
            # ASGI断连时仍完成取消标记，并关闭上游模型HTTP连接。
            import anyio
            try:
                with anyio.CancelScope(shield=True):
                    worker.cancel()
                    try:await worker
                    except asyncio.CancelledError:pass
                    if not terminal:
                        await run_in_threadpool(repository.finish,user,turn,'cancelled','',None,None,49901,'已停止生成')
            finally:
                # 即使数据库暂时不可写，也必须释放进程并发名额；占位由租约兜底恢复。
                _active.discard(user_key)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})
