"""HS256 JWTs backed by revocable server sessions; keys shared through a private database table."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from ..core.store import connect
from ..core.service import Problem


def key():
    configured=os.getenv('ARCSYNC_JWT_SECRET')
    if configured:
        if len(configured)<32:raise Problem('JWT 密钥至少需要32个字符',503)
        return configured.encode()
    with connect() as db:
        existing=db.execute('SELECT secret FROM jwt_keys WHERE id=1').fetchone()
        if existing:return existing[0].encode()
        db.execute('INSERT OR IGNORE INTO jwt_keys(id,secret) VALUES(1,?)',(secrets.token_urlsafe(48),))
        return db.execute('SELECT secret FROM jwt_keys WHERE id=1').fetchone()[0].encode()


def b64(value):return base64.urlsafe_b64encode(value).rstrip(b'=').decode()


def issue(identifier,user):
    header=b64(b'{"alg":"HS256","typ":"JWT"}')
    claims={'iss':'arcsync','aud':'arcsync','sub':user['user_id'],'jti':identifier,'role':user['access_role'],'iat':int(time.time()),'exp':int(time.time())+28800}
    body=header+'.'+b64(json.dumps(claims,separators=(',',':')).encode())
    return body+'.'+b64(hmac.new(key(),body.encode(),hashlib.sha256).digest())


def decode(token):
    try:
        if not token or len(token)>4096:raise ValueError()
        head,body,signature=token.split('.')
        if not hmac.compare_digest(signature,b64(hmac.new(key(),(head+'.'+body).encode(),hashlib.sha256).digest())):raise ValueError()
        header=json.loads(base64.urlsafe_b64decode(head+'='*(-len(head)%4)))
        claims=json.loads(base64.urlsafe_b64decode(body+'='*(-len(body)%4)))
        if header!={'alg':'HS256','typ':'JWT'} or claims['iss']!='arcsync' or claims['aud']!='arcsync' or claims['exp']<=time.time() or claims['iat']>time.time()+30:raise ValueError()
        return claims
    except (ValueError,KeyError,TypeError):raise Problem('请先登录或重新登录',401) from None


def request_token(request):
    authorization=request.headers.get('authorization','')
    return authorization[7:] if authorization.startswith('Bearer ') else request.cookies.get('arcsync_session')


def session_id(request):return decode(request_token(request))['jti']
