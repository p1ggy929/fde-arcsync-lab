"""ASGI entry point: PYTHONPATH=src python -m arcsync.main."""
import argparse
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from .api.routes import router
from .api.agent_routes import router as agent_router
from .api.security import current_user
from .core.store import ROOT, init_db
from .core.service import Problem

FRONTEND=ROOT/'src'/'frontend'

@asynccontextmanager
async def lifespan(app):
    init_db()
    from .agents.repository import recover_interrupted
    recover_interrupted()
    yield

app=FastAPI(title='ArcSync 服务层',version='0.3.0',lifespan=lifespan,
            description='员工门户与管理后台共享 HTTP 服务，账号决定端及岗位权限。',
            docs_url=None,redoc_url=None,openapi_url='/api/openapi.json',
            swagger_ui_parameters={'persistAuthorization':False})
app.include_router(router)
app.include_router(agent_router)

@app.exception_handler(Problem)
async def problem_handler(request,exc):
    return JSONResponse({'error':exc.message},status_code=exc.status)

@app.exception_handler(RequestValidationError)
async def validation_handler(request,exc):
    return JSONResponse({'error':'请求参数格式无效'},status_code=400)

@app.exception_handler(Exception)
async def unexpected_handler(request,exc):
    logging.getLogger('arcsync').error('Request failed: %s',type(exc).__name__)
    return JSONResponse({'error':'操作未完成，请刷新检查后重试'},status_code=500)

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
    files={'':'index.html','index.html':'index.html','portal':'index.html','admin':'index.html',
           'app.js':'app.js','style.css':'style.css','favicon.svg':'favicon.svg'}
    if page not in files:
        raise Problem('页面或接口不存在',404)
    return FileResponse(FRONTEND/files[page])

def main():
    import uvicorn
    parser=argparse.ArgumentParser(description='ArcSync 双端平台 · FastAPI')
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False)

if __name__=='__main__':
    main()
