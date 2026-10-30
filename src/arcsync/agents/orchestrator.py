"""Autonomous tool selection with server-enforced access draft prerequisites."""
import asyncio
import re
import time
import uuid
from starlette.concurrency import run_in_threadpool
from ..core.store import connect
from ..core.service import Problem, check_active
from ..data_models.config import ModelError, ModelSettings
from ..data_models.providers import ModelClient
from ..data_models.knowledge import valid_sources
from .tools import ToolBox
from . import repository

from .policy import POLICY, EXECUTION_RULES
from .harness import AgentHarness

class Orchestrator:
    def __init__(self,user,config=None,llm=None,embedding=None):
        self.user=user;self.config=config or ModelSettings.load()
        self.config.validate()
        self.llm=llm or ModelClient(self.config.llm)
        self.embedding=embedding or ModelClient(self.config.embedding)

    async def run(self,question,guard=None,history=None,persist=True,create_authorized=False):
        # 历史只允许由服务端仓库提供，作为上下文，不作为本轮的事实依据。
        history=history or []
        from ..admin.repository import prompt
        system_prompt=(await run_in_threadpool(prompt))['content']+'\n'+EXECUTION_RULES
        identifier=await run_in_threadpool(repository.start_run,self.user,question) if persist else uuid.uuid4().hex
        tools=ToolBox(self.user,self.embedding,self.config.allow_restricted,create_authorized)
        usage={'input_tokens':0,'output_tokens':0,'model_calls':0,'tool_calls':0}
        completed=False;answer='';harness=AgentHarness(self.user,identifier,self.config,getattr(self,'interaction',{}))
        def add_usage(value):
            usage['model_calls']+=1
            for key in ('input_tokens','output_tokens'):usage[key]+=int(value.get(key,0) or 0)
        last_check=0.0
        async def check(force=True):
            nonlocal last_check
            if not force and time.monotonic()-last_check<.4:return
            last_check=time.monotonic()
            if guard:await guard()
            def active():
                with connect() as db:check_active(db)
            await run_in_threadpool(active)
            if tools.sources:await run_in_threadpool(valid_sources,self.user,tools.sources,self.config.allow_restricted)
        async def step(kind,data):
            if persist:await run_in_threadpool(repository.append_step,identifier,kind,data)
            return {'event':kind,'data':data}
        async def finish(status,answer,sources,usage,error=''):
            # 多轮聊天由chat_messages持久化，不复制到按岗位归属的旧运行历史。
            if persist:await run_in_threadpool(repository.finish_run,self.user,identifier,status,answer,sources,usage,error)
        try:
            await check()
            yield {'event':'start','data':{'run_id':identifier,'provider':self.config.llm.provider,'model':self.config.llm.model}}
            yield await step('step',{'stage':'deciding','message':'正在判断需要检索、核对还是补充查阅信息'})
            messages=[{'role':'system','content':system_prompt+'\n历史回答仅用于理解上下文；本轮回答引用本轮来源。无工具调用时请直接给出最终答案或追问，不输出思考过程。最多5轮决策，可在同一轮请求互相独立的只读工具。'},*history,{'role':'user','content':question}]
            async for item in harness.run(self.llm,tools,messages,check,add_usage):
                kind=item['event']
                if kind=='answer':answer=item['data']['text']
                elif kind=='confirmation_required':
                    await finish('waiting_confirmation',item['data']['preview_text'],tools.sources,usage)
                    completed=True
                    yield item
                    return
                else:
                    if kind=='tool' and persist:await run_in_threadpool(repository.append_step,identifier,'tool',item['data'])
                    yield item
            await check()
            yield await step('sources',{'sources':tools.sources})
            yield await step('step',{'stage':'generate','message':'正在展示本轮回答；引用仍须人工核对'})
            for position in range(0,len(answer),40):
                await check(False)
                yield {'event':'token','data':{'text':answer[position:position+40],'draft':True}}
                await asyncio.sleep(0)
            await check()
            labels=set(re.findall(r'\[C(\d+)\]',answer));allowed={s['label'][1:] for s in tools.sources}
            if not answer.strip() or (allowed and not labels) or not labels<=allowed:
                raise ModelError('回答未通过引用编号校验，草稿已撤回；请调整问题重试。')
            if harness.status!='limit_reached':await harness.state('completed')
            await finish('completed',answer,tools.sources,usage)
            completed=True
            yield {'event':'done','data':{'run_id':identifier,'answer':answer,'sources':tools.sources,'usage':usage,
                                        'notice':'来源及版本已核对，内容仍须人工复核；未替代审核、开放或处置决定。'}}
        except (ModelError,Problem) as error:
            await harness.state('failed')
            message=error.message if isinstance(error,Problem) else str(error)
            await finish('failed','',tools.sources,usage,message)
            completed=True
            yield {'event':'error','data':{'run_id':identifier,'error':message,'discard_draft':True}}
        except asyncio.CancelledError:
            raise
        except Exception:
            await harness.state('failed')
            message='智能查阅未完成，请检查模型与数据库连接后重试。'
            await finish('failed','',[],usage,message)
            completed=True
            yield {'event':'error','data':{'run_id':identifier,'error':message,'discard_draft':True}}
        finally:
            if not completed:
                # Keep cancellation cleanup alive under ASGI task cancellation.
                import anyio
                with anyio.CancelScope(shield=True):
                    if harness.status!='waiting_confirmation':await harness.state('cancelled')
                    await finish('cancelled','',[],usage,'客户端已取消或连接中断')
