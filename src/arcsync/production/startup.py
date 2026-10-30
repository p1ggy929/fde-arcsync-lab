"""生产启动检查失败即停止，不自动删除账号或修改已有数据。"""
import os
from . import enabled
from .controls import redis_client, number, price


def preflight():
    if not enabled():return
    required=('ARCSYNC_REDIS_URL','ARCSYNC_JWT_SECRET','ARCSYNC_ALLOWED_HOSTS',
              'ARCSYNC_INPUT_USD_PER_MILLION','ARCSYNC_OUTPUT_USD_PER_MILLION','ARCSYNC_EMBEDDING_USD_PER_MILLION')
    if any(not os.getenv(name) for name in required):raise RuntimeError('生产配置不完整，请检查生产部署说明')
    from urllib.parse import urlsplit
    redis_url=urlsplit(os.environ['ARCSYNC_REDIS_URL'])
    if redis_url.scheme not in ('redis','rediss') or not redis_url.hostname or not redis_url.password:
        raise RuntimeError('生产Redis必须配置带密码的redis或rediss地址')
    secret=os.environ['ARCSYNC_JWT_SECRET']
    if len(secret)<48 or len(set(secret))<16:raise RuntimeError('生产JWT密钥须为独立随机生成的至少48字符密钥')
    for kind in ('input','output','embedding'):price(kind)
    for key,default in [('ARCSYNC_GLOBAL_CONCURRENCY',4),('ARCSYNC_DAILY_TOKENS',1000000),('ARCSYNC_USER_DAILY_TOKENS',100000),('ARCSYNC_DAILY_MICRO_USD',10000000),('ARCSYNC_USER_DAILY_MICRO_USD',1000000),('ARCSYNC_USAGE_RETENTION_DAYS',90)]:number(key,default)
    if not redis_client().ping():raise RuntimeError('Redis资源管控不可用')


def check_accounts():
    if not enabled():return
    import hashlib
    import hmac
    from ..core.store import connect, ROLES
    with connect() as db:
        for row in db.execute('SELECT * FROM user_accounts WHERE enabled=1'):
            if row['username'] in ROLES:raise RuntimeError('生产库仍存在启用的演示账号，请显式停用并撤销会话')
            for password in {'ArcSync2026!',os.getenv('ARCSYNC_DEMO_PASSWORD','ArcSync2026!')}:
                candidate=hashlib.pbkdf2_hmac('sha256',password.encode(),row['salt'].encode(),120000).hex()
                if hmac.compare_digest(candidate,row['password']):raise RuntimeError('生产账号使用已知演示口令，请轮换密码')
        if not db.execute("SELECT 1 FROM user_accounts WHERE enabled=1 AND role IN ('director','protector')").fetchone():
            raise RuntimeError('生产库缺少具名管理账号，请先通过本机脚本创建')
        if db.execute('SELECT 1 FROM businesses WHERE sample=1').fetchone():
            raise RuntimeError('生产库含演示业务，请使用独立生产库或完成受控清理')
