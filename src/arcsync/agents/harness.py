"""Small agent harness: decide -> execute -> feed results -> decide, with durable HITL."""
import asyncio
import json
import time
from starlette.concurrency import run_in_threadpool
from ..core.service import Problem
from ..data_models.config import ModelError
from . import access, harness_store


class AgentHarness:
    def __init__(self,user,identifier,config,interaction=None):
        self.user=user;self.id=identifier;self.config=config;self.interaction=interaction or {}
        self.budget=min(harness_store.MAX_ROUNDS,max(1,config.max_rounds))
        self.round_no=0;self.status='running'

    async def state(self,status):
        self.status=status
        await run_in_threadpool(harness_store.state,self.id,status,self.round_no)

    async def run(self,llm,tools,messages,check,add_usage):
        await run_in_threadpool(harness_store.start,self.user,self.id,self.budget,self.interaction)
        previous=None;tool_count=0
        for round_no in range(1,self.budget+1):
            self.round_no=round_no
            await self.state('running');await check()
            if len(json.dumps(messages,ensure_ascii=False))>60000:raise ModelError('工具上下文达到上限，请缩小问题范围。')
            schemas=await tools.schemas();offered={s['function']['name'] for s in schemas}
            decision=await run_in_threadpool(harness_store.begin,self.user,self.id,round_no,'decision','__decision__')
            started=time.perf_counter()
            try:
                message,usage=await llm.chat(messages,tools=schemas);add_usage(usage)
                calls=message.get('tool_calls') or []
                selected=[c['function']['name'] for c in calls]
                await run_in_threadpool(harness_store.finish_log,decision,'completed',(time.perf_counter()-started)*1000,{'message':'调用：'+', '.join(selected) if selected else '直接回答／询问必要信息'})
            except BaseException as error:
                import anyio
                with anyio.CancelScope(shield=True):
                    await run_in_threadpool(harness_store.finish_log,decision,'cancelled' if isinstance(error,asyncio.CancelledError) else 'failed',(time.perf_counter()-started)*1000,{'error':'决策调用中断' if isinstance(error,asyncio.CancelledError) else '模型决策失败'})
                raise
            yield {'event':'decision','data':{'run_id':self.id,'round_no':round_no,'tools':selected,'max_rounds':self.budget}}
            if not calls:
                yield {'event':'answer','data':{'text':message.get('content','')}}
                return
            if tool_count+len(calls)>self.config.max_tools:raise ModelError('工具调用达到上限，请缩小问题范围。')
            messages.append(message)
            blocked_write=len(calls)>1 and 'create_access_draft' in selected
            for call in calls:
                await check();fn=call['function'];name=fn['name'];arguments=fn['arguments']
                if isinstance(arguments,str):
                    try:arguments=json.loads(arguments)
                    except ValueError:arguments={'invalid_json':arguments[:1000]}
                tool_count+=1
                log=await run_in_threadpool(harness_store.begin,self.user,self.id,round_no,'tool',name,arguments,call.get('id',''))
                started=time.perf_counter()
                yield {'event':'tool','data':{'name':name,'status':'running','round_no':round_no}}
                try:
                    key=name+json.dumps(arguments,sort_keys=True)
                    if key==previous and name!='create_access_draft':raise ModelError('模型重复请求相同工具，已停止循环，请调整问题。')
                    previous=key
                    if name not in offered:raise Problem('当前阶段不允许调用此工具，请先完成前置条件',403)
                    if blocked_write:raise Problem('创建草稿必须单独在校验返回后的下一轮调用',409)
                    tools.execution_context={'run_id':self.id,'round_no':round_no,**self.interaction}
                    result=await tools.execute(name,arguments)
                except Problem as error:result={'status':'rejected','error':error.message,'code':error.status*100+1}
                except BaseException as error:
                    import anyio
                    with anyio.CancelScope(shield=True):
                        await run_in_threadpool(harness_store.finish_log,log,'cancelled' if isinstance(error,asyncio.CancelledError) else 'failed',(time.perf_counter()-started)*1000,{'error':'工具已中断'})
                    raise
                state='waiting_confirmation' if result.get('status')=='waiting_confirmation' else 'rejected' if 'error' in result else 'completed'
                await run_in_threadpool(harness_store.finish_log,log,state,(time.perf_counter()-started)*1000,result)
                await run_in_threadpool(access.tool_audit,self.user,self.id,name,arguments,result)
                messages.append({'role':'tool','tool_call_id':call['id'],'name':name,'content':json.dumps(result,ensure_ascii=False)})
                yield {'event':'tool','data':{'name':name,'status':state,'round_no':round_no,'message':result.get('error','工具执行完成'),'result':result}}
                if state=='waiting_confirmation':
                    self.status=state
                    preview=result['preview_text'];confirmation=result['confirmation']
                    for position in range(0,len(preview),40):
                        await check()
                        yield {'event':'preview_delta','data':{'text':preview[position:position+40]}}
                        await asyncio.sleep(0)
                    yield {'event':'confirmation_required','data':{**confirmation,'preview_text':preview,'sources':tools.sources}}
                    return
        await self.state('limit_reached')
        labels=' '.join('['+s['label']+']' for s in tools.sources)
        yield {'event':'answer','data':{'text':'已达到本次5轮决策上限，当前结果已记录。尚未确认完成的操作不会视为成功；请缩小范围或补充信息后继续。'+labels,'limit_reached':True}}
