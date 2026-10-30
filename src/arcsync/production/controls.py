"""Redis原子共享限流、并发租约及保守预算预留，不存储提问或凭证。"""
import hashlib
import json
import os
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from ..core.service import Problem
from . import enabled

actor=ContextVar('model_actor',default=None)
observer=ContextVar('evaluation_model_usage',default=None)


def redis_client():
    import redis
    return redis.Redis.from_url(os.environ['ARCSYNC_REDIS_URL'],decode_responses=True,
                               socket_connect_timeout=2,socket_timeout=3)


def number(name,default):
    value=int(os.getenv(name,str(default)))
    if value<=0:raise RuntimeError(name+'必须为正整数')
    return value


def run(script,keys,args):
    try:return redis_client().eval(script,len(keys),*keys,*args)
    except Exception as error:
        if isinstance(error,Problem):raise
        raise Problem('资源管控暂不可用，请稍后重试',503) from None


def rate(key,limit,seconds):
    count=run("local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n",['arcsync:rate:'+key],[seconds])
    if count>limit:raise Problem('请求过于频繁，请稍后再试',429)


LEASE="""
redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',ARGV[1]);
redis.call('ZREMRANGEBYSCORE',KEYS[2],'-inf',ARGV[1]);
if redis.call('ZCARD',KEYS[1])>=tonumber(ARGV[3]) or redis.call('ZCARD',KEYS[2])>=1 then return 0 end;
redis.call('ZADD',KEYS[1],ARGV[2],ARGV[4]); redis.call('ZADD',KEYS[2],ARGV[2],ARGV[4]);
redis.call('EXPIRE',KEYS[1],660);redis.call('EXPIRE',KEYS[2],660);return 1
"""


def acquire(user):
    token=uuid.uuid4().hex
    keys=['arcsync:requests','arcsync:requests:'+user]
    now=time.time()
    if not run(LEASE,keys,[now,now+600,number('ARCSYNC_GLOBAL_CONCURRENCY',4),token]):
        raise Problem('当前账号正在处理任务或平台繁忙，请稍后重试',429)
    return keys,token


def release(lease):
    keys,token=lease
    run("redis.call('ZREM',KEYS[1],ARGV[1]);redis.call('ZREM',KEYS[2],ARGV[1]);return 1",keys,[token])


RESERVE="""
for i=1,4 do
 if tonumber(redis.call('GET',KEYS[i]) or '0')+tonumber(ARGV[(i<=2) and 1 or 2])>tonumber(ARGV[i+2]) then return 0 end
end
for i=1,4 do redis.call('INCRBY',KEYS[i],ARGV[(i<=2) and 1 or 2]);redis.call('EXPIRE',KEYS[i],172800) end
redis.call('HSET',KEYS[5],'event',ARGV[7]);redis.call('EXPIRE',KEYS[5],ARGV[8]);return 1
"""


def price(kind):
    value=Decimal(os.environ['ARCSYNC_'+kind.upper()+'_USD_PER_MILLION'])
    if not value.is_finite() or value<0:raise RuntimeError('模型价格必须为非负有限数')
    return value


def reserve(endpoint,kind,payload,output=0):
    if not enabled():return None
    user=actor.get()
    if not user:raise Problem('模型调用缺少具名用户上下文',403)
    # UTF-8字节数保守覆盖常规分词，并额外预留工具/消息封装；不宣称是供应商账单。
    inputs=len(json.dumps(payload,ensure_ascii=False).encode())+4096
    tokens=inputs+output
    if tokens>number('ARCSYNC_MAX_CALL_RESERVED_TOKENS',200000):raise Problem('模型请求超出单次资源上限，请缩小范围',413)
    cost=int((Decimal(inputs)*price('embedding' if kind=='embed' else 'input')+Decimal(output)*price('output')).to_integral_value(rounding=ROUND_CEILING))
    day=datetime.now(timezone.utc).strftime('%Y%m%d');identifier=uuid.uuid4().hex
    keys=[f'arcsync:budget:{day}:tokens',f'arcsync:budget:{day}:{user}:tokens',
          f'arcsync:budget:{day}:usd',f'arcsync:budget:{day}:{user}:usd','arcsync:usage:'+identifier]
    entry={'id':identifier,'user_id':user,'provider':endpoint.provider,'model':endpoint.model,'kind':kind,
           'reserved_tokens':tokens,'reserved_micro_usd':cost,'status':'reserved','created':time.time()}
    args=[tokens,cost,number('ARCSYNC_DAILY_TOKENS',1000000),number('ARCSYNC_USER_DAILY_TOKENS',100000),
          number('ARCSYNC_DAILY_MICRO_USD',10000000),number('ARCSYNC_USER_DAILY_MICRO_USD',1000000),
          json.dumps(entry),number('ARCSYNC_USAGE_RETENTION_DAYS',90)*86400]
    if not run(RESERVE,keys,args):raise Problem('模型调用额度已用尽，请联系管理员',429)
    # 每天每项阈值仅记录一次告警，由生产日志告警系统转发给值守人。
    import logging
    client=redis_client()
    for key,limit in zip(keys[:4],args[2:6]):
        if int(client.get(key) or 0)>=int(limit)*0.8 and client.set(key+':warned','1',nx=True,ex=172800):
            logging.getLogger('arcsync.budget').warning('模型预算已达80%% budget_key=%s',key)
    observed=observer.get()
    if observed is not None:observed.append(entry)
    return keys[-1],entry,time.monotonic()


def settle(ticket,status,usage=None):
    if not ticket:return
    key,entry,started=ticket
    entry.update(status=status,duration_ms=round((time.monotonic()-started)*1000),reported_usage=usage or {})
    # 未知、失败和取消仍保留预留额，避免退款后反复调用绕过预算。
    run("redis.call('HSET',KEYS[1],'event',ARGV[1]);return 1",[key],[json.dumps(entry)])


def usage_summary():
    client=redis_client();day=datetime.now(timezone.utc).strftime('%Y%m%d')
    values=client.mget(f'arcsync:budget:{day}:tokens',f'arcsync:budget:{day}:usd')
    entries=[]
    # 有界读取；明细不含原问题、正文、密钥或模型地址。
    for key in client.scan_iter('arcsync:usage:*',count=100):
        raw=client.hget(key,'event')
        if raw:entries.append(json.loads(raw))
        if len(entries)>=100:break
    return {'day_utc':day,'reserved_tokens':int(values[0] or 0),'reserved_micro_usd':int(values[1] or 0),
            'notice':'保守预留值，不等于供应商实际账单；失败和取消不退预留。明细最多100条，非完整导出。',
            'items':entries}


def governed(method):
    """所有非流式模型调用都在实际联网前预留预算。"""
    import functools
    from starlette.concurrency import run_in_threadpool
    @functools.wraps(method)
    async def wrapped(self,*args,**kwargs):
        kind='embed' if method.__name__=='embed' else 'chat'
        ticket=await run_in_threadpool(reserve,self.endpoint,kind,[args,kwargs],0 if kind=='embed' else 1600)
        result=None;status='failed';usage=None
        try:
            result=await method(self,*args,**kwargs)
            status='completed'
            if kind=='chat':usage=result[1]
            return result
        finally:
            await run_in_threadpool(settle,ticket,status,usage)
    return wrapped


def governed_stream(method):
    import functools
    from starlette.concurrency import run_in_threadpool
    @functools.wraps(method)
    async def wrapped(self,messages,max_tokens=1600):
        from contextlib import aclosing
        ticket=await run_in_threadpool(reserve,self.endpoint,'stream',messages,max_tokens)
        status='failed';usage=None
        try:
            async with aclosing(method(self,messages,max_tokens)) as stream:
                async for item in stream:
                    if 'usage' in item:usage=item['usage']
                    yield item
            status='completed'
        finally:
            await run_in_threadpool(settle,ticket,status,usage)
    return wrapped
