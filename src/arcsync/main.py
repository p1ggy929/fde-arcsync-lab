"""ASGI entry point: PYTHONPATH=src python -m arcsync.main."""
import argparse
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from .api.routes import router
from .api.agent_routes import router as agent_router
from .chat.routes import router as chat_router
from starlette.exceptions import HTTPException
from .api.security import current_user
from .core.store import ROOT, init_db
from .core.service import Problem

FRONTEND=ROOT/'src'/'frontend'

@asynccontextmanager
async def lifespan(app):
    from .production.startup import preflight, check_accounts
    preflight()
    init_db()
    check_accounts()
    from .agents.repository import recover_interrupted
    recover_interrupted()
    yield

app=FastAPI(title='ArcSync 服务层',version='0.3.0',lifespan=lifespan,
            description='员工门户与管理后台共享 HTTP 服务，账号决定端及岗位权限。',
            docs_url=None,redoc_url=None,openapi_url='/api/openapi.json',
            swagger_ui_parameters={'persistAuthorization':False})
from .production.middleware import ProductionMiddleware
app.add_middleware(ProductionMiddleware)
app.include_router(router)
app.include_router(agent_router)
app.include_router(chat_router)
from .admin.routes import router as admin_router
app.include_router(admin_router)
from .agents.access_routes import router as access_router
app.include_router(access_router)

@app.exception_handler(Problem)
async def problem_handler(request,exc):
    # 保留error别名兼容旧页面，新接口统一使用code/message/data。
    return JSONResponse({'code':exc.status*100+1,'message':exc.message,'data':None,'error':exc.message},status_code=exc.status)

@app.exception_handler(RequestValidationError)
async def validation_handler(request,exc):
    return JSONResponse({'code':40001,'message':'请求参数格式无效','data':None,'error':'请求参数格式无效'},status_code=400)

@app.exception_handler(Exception)
async def unexpected_handler(request,exc):
    logging.getLogger('arcsync').error('Request failed: %s',type(exc).__name__)
    return JSONResponse({'code':50001,'message':'服务处理失败，请稍后重试','data':None,'error':'服务处理失败，请稍后重试'},status_code=500)

@app.exception_handler(HTTPException)
async def http_error_handler(request,exc):
    # 框架产生的404/405等错误也返回中文，避免默认英文detail响应。
    message={404:'页面或接口不存在',405:'请求方法不支持'}.get(exc.status_code,'请求未完成，请检查请求参数')
    return JSONResponse({'code':exc.status_code*100+1,'message':message,'data':None,'error':message},status_code=exc.status_code,headers=exc.headers)

@app.middleware('http')
async def headers(request: Request,call_next):
    response=await call_next(request)
    response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
        'Referrer-Policy':'same-origin',
        'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"})
    return response

@app.get('/api/docs',include_in_schema=False)
def api_documentation():
    # Local, dependency-free documentation: works offline under the same CSP.
    from fastapi.responses import HTMLResponse
    return HTMLResponse('<!doctype html><meta charset="utf-8"><title>ArcSync API</title><h1>ArcSync HTTP API</h1><p>接口与鉴权说明见项目 docs/api.md。</p><p><a href="/api/openapi.json">查看 OpenAPI 接口定义</a> · <a href="/">返回欢迎页</a></p>')

@app.get('/{page:path}',include_in_schema=False)
def frontend(page: str,request: Request):
    if page in ('portal','admin'):
        try:
            user=current_user(request)
        except Problem:
            return RedirectResponse('/',status_code=303)
        if page != user['home'].lstrip('/'):
            return RedirectResponse(user['home'],status_code=303)
    files={'admin/manage.css':'manage.css','admin/manage.js':'manage.js','':'index.html','index.html':'index.html','portal':'index.html','admin':'index.html',
           'chat.js':'chat.js','chat.css':'chat.css','app.js':'app.js','style.css':'style.css','favicon.svg':'favicon.svg'}
    if page not in files:
        raise Problem('页面或接口不存在',404)
    from .production import enabled as production
    if production() and page=='app.js':
        from fastapi.responses import Response
        import re
        source=(FRONTEND/'app.js').read_text()
        source=re.sub(r'<div class="account-examples">.*?</div></div>', '',source)
        source=source.replace("form.elements.password.value='ArcSync2026!'", "form.elements.password.value=''")
        return Response(source,media_type='text/javascript')
    return FileResponse(FRONTEND/files[page])

def main():
    import uvicorn
    parser=argparse.ArgumentParser(description='ArcSync 双端平台 · FastAPI')
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False)

if __name__=='__main__':
    main()
