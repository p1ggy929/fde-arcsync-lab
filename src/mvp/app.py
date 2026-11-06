"""将现有档案、鉴权与业务流转规则接到轻量演示页面。"""
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, RedirectResponse
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from arcsync.agents.access import business_visible
from arcsync.api.jwt_tokens import decode
from arcsync.api.queries import document_page, search_documents
from arcsync.api.security import current_user, login
from arcsync.core.service import Problem, business, mutate
from arcsync.core.store import ROOT, connect, init_db

from . import views

COOKIE = 'arcsync_mvp_session'
ROLES = ('representative', 'organizer', 'reviewer')
ACTIONS = ('create', 'authorize', 'accept', 'cite', 'submit', 'approve', 'return', 'deliver')
HERE = Path(__file__).resolve().parent


async def read_form(request: Request):
    # 仅接收同源表单，避免外部站点使用本机演示会话代办业务。
    origin = request.headers.get('origin')
    if origin != str(request.base_url).rstrip('/'):
        raise Problem('表单来源与当前工作台不一致，请刷新后重试', 403)
    content_type = request.headers.get('content-type', '').partition(';')[0]
    if content_type != 'application/x-www-form-urlencoded':
        raise Problem('请通过工作台表单提交', 415)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 32768:
            raise Problem('表单内容过长', 413)
        body.extend(chunk)
    try:
        fields = parse_qs(body.decode('utf-8'), keep_blank_values=True, max_num_fields=12)
    except (UnicodeError, ValueError):
        raise Problem('表单格式无效') from None
    if any(len(values) != 1 for values in fields.values()):
        raise Problem('表单含重复字段')
    return {key: values[0] for key, values in fields.items()}


def mvp_user(request: Request):
    token = request.cookies.get(COOKIE)
    if not token:
        raise Problem('请先登录演示账号', 401)
    # 复用现有 JWT 和会话校验。单独的 Cookie 名可让主站与 MVP 同时登录。
    headers = [(b'authorization', ('Bearer ' + token).encode('utf-8'))]
    user = current_user(Request({**request.scope, 'headers': headers}))
    if user['role'] not in ROLES:
        raise Problem('请选择 MVP 提供的演示岗位', 403)
    return user


def visible_request(db, user, identifier):
    item = business(db, identifier)
    if not business_visible(db, user, item):
        raise Problem('申请不存在或不在当前岗位的查阅范围内', 404)
    return item


def pending_handoff(db, identifier):
    row = db.execute(
        "SELECT * FROM handoffs WHERE business_id=? AND status='待接手'", (identifier,),
    ).fetchone()
    return dict(row) if row else None


def create_app():
    @asynccontextmanager
    async def lifespan(_app):
        init_db()
        yield

    app = FastAPI(title='云州档案 MVP', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost'])

    @app.middleware('http')
    async def response_headers(request, call_next):
        response = await call_next(request)
        response.headers.update({
            'Cache-Control': 'no-store',
            'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'same-origin',
            'Content-Security-Policy': "default-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
        })
        return response

    @app.exception_handler(Problem)
    async def problem_page(request, error):
        if error.status == 401 and request.method == 'GET':
            return RedirectResponse('/login', status_code=303)
        return views.error(error.message, error.status)

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        message = '这个页面不存在。' if error.status_code == 404 else '此页面不支持该操作。'
        return views.error(message, error.status_code)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return views.error('页码须为正整数，关键词最多 200 字，请检查后重试。', 400)

    @app.get('/mvp.css')
    def css():
        return FileResponse(HERE / 'mvp.css')

    @app.get('/tokens.css')
    def tokens():
        return FileResponse(HERE / 'tokens.css')

    @app.get('/favicon.svg')
    def favicon():
        return FileResponse(ROOT / 'src' / 'frontend' / 'favicon.svg', media_type='image/svg+xml')

    @app.get('/login')
    def login_page():
        return views.sign_in()

    @app.post('/login')
    def sign_in(request: Request, data: dict = Depends(read_form)):
        if data.get('username') not in ROLES:
            raise Problem('请选择演示岗位')
        try:
            old_session = decode(request.cookies.get(COOKIE))['jti']
        except Problem as error:
            if error.status != 401:
                raise
            old_session = ''
        user, token = login({**data, '_old_token': old_session})
        destination = '/' if user['role'] == 'organizer' else '/requests'
        response = RedirectResponse(destination, status_code=303)
        response.set_cookie(COOKIE, token, max_age=28800, httponly=True, samesite='strict')
        return response

    @app.post('/logout')
    def sign_out(request: Request, _data: dict = Depends(read_form), _user: dict = Depends(mvp_user)):
        session = decode(request.cookies[COOKIE])['jti']
        with connect() as db:
            db.execute('DELETE FROM session_identities WHERE token=?', (session,))
            db.execute('DELETE FROM sessions WHERE token=?', (session,))
        response = RedirectResponse('/login', status_code=303)
        response.delete_cookie(COOKIE)
        return response

    @app.get('/')
    def catalog(q: str = Query('', max_length=200), user: dict = Depends(mvp_user)):
        with connect() as db:
            documents = search_documents(db, user, {'q': [q]})['documents']
        return views.catalog(user, q, documents)

    @app.get('/archives/{identifier}')
    def archive(identifier: str, page: int = Query(1, ge=1), user: dict = Depends(mvp_user)):
        with connect() as db:
            record = document_page(db, user, identifier, {'page': [str(page)]})
            candidates = []
            if user['role'] == 'organizer':
                for row in db.execute("SELECT id,title,revision FROM businesses WHERE status='定位中' AND owner='organizer'"):
                    if not pending_handoff(db, row['id']):
                        candidates.append(dict(row))
        return views.archive(user, record, candidates)

    @app.get('/requests')
    def requests(user: dict = Depends(mvp_user)):
        with connect() as db:
            items = [dict(row) for row in db.execute('SELECT * FROM businesses ORDER BY created DESC, id') if business_visible(db, user, row)]
        return views.requests(user, items)

    @app.get('/requests/{identifier}')
    def request_detail(identifier: str, user: dict = Depends(mvp_user)):
        with connect() as db:
            item = visible_request(db, user, identifier)
            pending = pending_handoff(db, identifier)
            events = db.execute('SELECT * FROM events WHERE business_id=? ORDER BY id DESC', (identifier,)).fetchall()
        return views.request_detail(user, item, pending, events)

    @app.post('/actions/{action}')
    def apply_action(action: str, data: dict = Depends(read_form), user: dict = Depends(mvp_user)):
        if action not in ACTIONS:
            raise Problem('MVP 没有此操作', 404)
        if action == 'create' and user['role'] != 'representative':
            raise Problem('请使用业务查询代表账号提交申请', 403)
        for key in ('revision', 'scan_page', 'paper_page', 'id'):
            if key in data:
                try:
                    data[key] = int(data[key])
                except ValueError:
                    raise Problem('页码或业务编号格式无效') from None
        if action == 'cite':
            if data.get('confirmed') != 'yes':
                raise Problem('请先人工核对卷页，再确认引用')
            identifier, separator, revision = data.get('selection', '').partition(':')
            if not identifier or not separator or not (revision.isascii() and revision.isdecimal() and len(revision) <= 10):
                raise Problem('请选择有效的申请记录')
            data.update(business_id=identifier, revision=int(revision))
            data['correction_checked'] = data.get('correction_checked') == 'yes'

        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            identifier = data.get('business_id')
            if action == 'accept':
                handoff = db.execute('SELECT business_id FROM handoffs WHERE id=?', (data.get('id'),)).fetchone()
                identifier = handoff['business_id'] if handoff else None
            if action != 'create' and not identifier:
                raise Problem('请选择查阅申请', 404)
            result = mutate(db, user, action, data)
        return RedirectResponse(views.request_url(result.get('id', identifier)), status_code=303)

    return app
