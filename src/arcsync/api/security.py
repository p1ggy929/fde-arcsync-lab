"""Server-owned account routing; client portal selections never grant privileges."""
import hashlib
import hmac
import secrets
from datetime import datetime, timezone
from fastapi import Request, Depends, Security
from fastapi.security import APIKeyCookie
from ..core.store import connect, ROLES, now, audit
from ..core.service import Problem

cookie_scheme = APIKeyCookie(name='arcsync_session', auto_error=False)

EMPLOYEE_ROLES = {'organizer', 'representative'}

def identity(role, name):
    portal = 'employee' if role in EMPLOYEE_ROLES else 'admin'
    return {'role': role, 'name': name, 'label': ROLES[role][1],
            'portal': portal, 'home': '/portal' if portal == 'employee' else '/admin'}

def current_user(request: Request, _session: str | None = Security(cookie_scheme)):
    token = request.cookies.get('arcsync_session')
    with connect() as db:
        row = db.execute('SELECT s.created,u.role,u.name FROM sessions s JOIN users u ON u.role=s.role WHERE s.token=?', (token,)).fetchone()
    if not row or (datetime.now(timezone.utc)-datetime.fromisoformat(row['created'])).total_seconds()>28800:
        raise Problem('请先登录或重新登录', 401)
    return identity(row['role'], row['name'])

def admin_user(user=Depends(current_user)):
    if user['portal'] != 'admin':
        raise Problem('当前账号无权进入管理后台', 403)
    return user

def login(data):
    # Legacy role is retained for existing API callers; both fields are credentials,
    # not a way to select or override the portal assigned to an account.
    account = data.get('username', data.get('role', ''))
    with connect() as db:
        row = db.execute('SELECT * FROM users WHERE role=?', (str(account),)).fetchone()
        candidate = hashlib.pbkdf2_hmac('sha256', str(data.get('password','')).encode(), (row['salt'] if row else 'invalid').encode(),120000).hex()
        if not row or not hmac.compare_digest(candidate, row['password']):
            raise Problem('账号或密码不正确',401)
        token = secrets.token_urlsafe(40)
        db.execute('DELETE FROM sessions WHERE token=?', (data.get('_old_token',''),))
        db.execute('INSERT INTO sessions VALUES(?,?,?)', (token,account,now()))
        user = identity(account,row['name'])
        audit(db,user,'登录'+('员工门户' if user['portal']=='employee' else '管理后台'))
    return user, token
