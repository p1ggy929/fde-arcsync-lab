"""纯ASGI中间件：上下文覆盖整个SSE生命周期，租约在退出时释放。"""
import asyncio
import logging
import os
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from ..core.service import Problem
from . import enabled
from . import controls

PAID={'/chat','/api/agent/chat/stream','/api/admin/models/check','/api/admin/knowledge'}


class ProductionMiddleware:
    def __init__(self,app):self.app=app

    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or not enabled():
            return await self.app(scope,receive,send)
        request=Request(scope,receive)
        lease=None;context=None;started=False
        async def tracked(message):
            nonlocal started
            if message['type']=='http.response.start':started=True
            await send(message)
        try:
            hosts=os.environ['ARCSYNC_ALLOWED_HOSTS'].split(',')
            if request.url.hostname not in hosts:raise Problem('访问主机不被允许',400)
            if scope.get('scheme')!='https':raise Problem('生产服务仅允许HTTPS访问',400)
            path=scope['path'];ip=(scope.get('client') or ('unknown',))[0]
            if path not in ('/api/health','/api/ready'):
                await run_in_threadpool(controls.rate,'ip:'+ip,controls.number('ARCSYNC_HTTP_PER_MINUTE',120),60)
            if path=='/api/login':
                await run_in_threadpool(controls.rate,'login-ip:'+ip,controls.number('ARCSYNC_LOGIN_PER_MINUTE',10),60)
            if path in PAID and scope['method']=='POST':
                from ..api.security import current_user
                user=await run_in_threadpool(current_user,request)
                context=controls.actor.set(user['user_id'])
                await run_in_threadpool(controls.rate,'model:'+user['user_id'],12,60)
                lease=await run_in_threadpool(controls.acquire,user['user_id'])
                # 请求须在租约失效前终止；供应商单次请求仍受更短超时控制。
                async with asyncio.timeout(540):await self.app(scope,receive,tracked)
            else:await self.app(scope,receive,tracked)
        except Problem as error:
            if started:raise
            response=JSONResponse({'code':error.status*100+1,'message':error.message,'data':None},
                                  status_code=error.status,headers={'Cache-Control':'no-store'})
            await response(scope,receive,send)
        finally:
            if context is not None:controls.actor.reset(context)
            if lease:
                try:await run_in_threadpool(controls.release,lease)
                except Problem:logging.getLogger('arcsync').error('并发租约释放失败，等待自动过期')
