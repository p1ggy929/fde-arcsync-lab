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

def identity(role, name, user_id=None, username=None):
    portal = 'employee' if role in EMPLOYEE_ROLES else 'admin'
    return {'role': role, 'name': name, 'label': ROLES[role][1],
            'access_role':'employee' if role in EMPLOYEE_ROLES else 'admin','user_id':user_id,'username':username or role,'portal': portal, 'home': '/portal' if portal == 'employee' else '/admin'}

def current_user(request: Request, _session: str | None = Security(cookie_scheme)):
    from .jwt_tokens import decode, request_token
    claims=decode(request_token(request))
    token=claims['jti']
    with connect() as db:
        row = db.execute('SELECT s.created,u.role,u.name,u.user_id,u.username FROM sessions s JOIN session_identities i ON i.token=s.token JOIN user_accounts u ON u.user_id=i.user_id WHERE s.token=? AND u.enabled=1', (token,)).fetchone()
    if not row or row['user_id']!=claims['sub'] or (datetime.now(timezone.utc)-datetime.fromisoformat(row['created'])).total_seconds()>28800:
        raise Problem('请先登录或重新登录', 401)
    return identity(row['role'],row['name'],row['user_id'],row['username'])

def admin_user(user=Depends(current_user)):
    if user['portal'] != 'admin':
        raise Problem('当前账号无权进入管理后台', 403)
    return user

def login(data):
    # Legacy role is retained for existing API callers; both fields are credentials,
    # not a way to select or override the portal assigned to an account.
    account = data.get('username', data.get('role', ''))
    from ..production import enabled as production
    if production():
        from ..production.controls import rate
        rate('login-account:'+hashlib.sha256(str(account).encode()).hexdigest(),10,300)
    with connect() as db:
        row = db.execute('SELECT * FROM user_accounts WHERE username=? AND enabled=1', (str(account),)).fetchone()
        candidate = hashlib.pbkdf2_hmac('sha256', str(data.get('password','')).encode(), (row['salt'] if row else 'invalid').encode(),120000).hex()
        if not row or not hmac.compare_digest(candidate, row['password']):
            import logging
            logging.getLogger('arcsync.security').warning('登录失败 account_hash=%s',hashlib.sha256(str(account).encode()).hexdigest()[:16])
            raise Problem('账号或密码不正确',401)
        token = secrets.token_urlsafe(40)
        db.execute('DELETE FROM session_identities WHERE token=?',(data.get('_old_token',''),))
        db.execute('DELETE FROM sessions WHERE token=?', (data.get('_old_token',''),))
        db.execute('INSERT INTO sessions VALUES(?,?,?)', (token,row['role'],now()))
        db.execute('INSERT INTO session_identities VALUES(?,?)',(token,row['user_id']))
        user=identity(row['role'],row['name'],row['user_id'],row['username'])
        audit(db,user,'登录'+('员工门户' if user['portal']=='employee' else '管理后台'))
    from .jwt_tokens import issue
    return user, issue(token,user)
