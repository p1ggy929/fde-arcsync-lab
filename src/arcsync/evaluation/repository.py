"""评测任务和结果持久化到管理SQLite；写入使用短事务与修订号。"""
import hashlib
import json
import os
import time
import uuid
from ..chat.repository import chat_connect
from ..core.store import now, ROOT, connect
from ..core.runtime import BUILD_ID
from ..core.service import Problem
from ..admin.repository import prompt
from .specs import suite, canonical
from .profiles import select, metadata
from .scoring import effective, gate


def db():
    connection=chat_connect()
    connection.executescript('''
    CREATE TABLE IF NOT EXISTS evaluation_jobs(id TEXT PRIMARY KEY,owner TEXT NOT NULL,status TEXT NOT NULL,profile TEXT NOT NULL,snapshot TEXT NOT NULL,created TEXT NOT NULL,updated TEXT NOT NULL,heartbeat REAL NOT NULL DEFAULT 0,worker TEXT NOT NULL DEFAULT '',cancel_requested INTEGER NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS evaluation_results(job_id TEXT NOT NULL,case_id INTEGER NOT NULL,result TEXT NOT NULL,review TEXT NOT NULL DEFAULT 'needs_review',reason TEXT NOT NULL DEFAULT '',reviewer TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(job_id,case_id));
    CREATE TABLE IF NOT EXISTS evaluation_reviews(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,case_id INTEGER NOT NULL,actor TEXT NOT NULL,decision TEXT NOT NULL,reason TEXT NOT NULL,created TEXT NOT NULL);
    ''')
    return connection


def create(user,profile,case_ids):
    config=select(profile);spec=suite();template=prompt()
    if not isinstance(case_ids,list) or not case_ids or len(case_ids)>30 or any(type(x) is not int or not 1<=x<=30 for x in case_ids) or len(set(case_ids))!=len(case_ids):raise Problem('请选择1至30个不重复的有效用例')
    snapshot={'code_version':BUILD_ID,'suite':spec,'case_ids':sorted(case_ids),'prompt':{'content':template['content'],'revision':template['revision']},
              'model':metadata(config),'profile':profile,'prices':{k:os.getenv(k,'') for k in ('ARCSYNC_INPUT_USD_PER_MILLION','ARCSYNC_OUTPUT_USD_PER_MILLION','ARCSYNC_EMBEDDING_USD_PER_MILLION')},
              'retrieval_version':'hybrid-faiss-current','scoring_version':'deterministic-human-v1',
              'fixture_hash':hashlib.sha256((ROOT/'src/arcsync/core/store.py').read_bytes()+(ROOT/'src/arcsync/evaluation/worker.py').read_bytes()).hexdigest()}
    identifier=uuid.uuid4().hex
    with db() as connection:
        connection.execute('BEGIN IMMEDIATE')
        if connection.execute("SELECT 1 FROM evaluation_jobs WHERE owner=? AND status IN ('queued','running')",(user['user_id'],)).fetchone():raise Problem('您已有待执行或运行中的评测，请先完成或停止',409)
        if connection.execute("SELECT COUNT(*) FROM evaluation_jobs WHERE status IN ('queued','running')").fetchone()[0]>=10:raise Problem('评测队列已满，请稍后再试',429)
        connection.execute('INSERT INTO evaluation_jobs(id,owner,status,profile,snapshot,created,updated) VALUES(?,?,?,?,?,?,?)',(identifier,user['user_id'],'queued',profile,canonical(snapshot),now(),now()))
    return detail(identifier)


def listed():
    with db() as connection:
        rows=[dict(r) for r in connection.execute('SELECT id,owner,status,profile,created,updated,cancel_requested,error FROM evaluation_jobs ORDER BY created DESC LIMIT 50')]
    return rows


def detail(identifier):
    with db() as connection:
        row=connection.execute('SELECT * FROM evaluation_jobs WHERE id=?',(identifier,)).fetchone()
        if not row:raise Problem('评测任务不存在',404)
        job=dict(row);job['snapshot']=json.loads(job['snapshot'])
        rows=[{**dict(r),'result':json.loads(r['result'])} for r in connection.execute('SELECT * FROM evaluation_results WHERE job_id=? ORDER BY case_id',(identifier,))]
    job['results']=rows;job['gate']=gate(job,rows)
    job['summary']=summary(rows);return job


def summary(rows):
    stats={key:sum(effective(row)==key for row in rows) for key in ('passed','failed','needs_review')}
    stats.update(completed_cases=len(rows),latency_ms=sum(row['result'].get('duration_ms',0) for row in rows),
                 input_tokens=sum(row['result'].get('usage',{}).get('input_tokens',0) for row in rows),
                 output_tokens=sum(row['result'].get('usage',{}).get('output_tokens',0) for row in rows),
                 reserved_micro_usd=sum(row['result'].get('reserved_micro_usd',0) for row in rows))
    latencies=sorted(row['result'].get('duration_ms',0) for row in rows)
    import math
    stats['p95_latency_ms']=latencies[math.ceil(len(latencies)*.95)-1] if latencies else None
    stats['mean_latency_ms']=sum(latencies)/len(latencies) if latencies else None
    def rate(ids):
        subset=[row for row in rows if row['case_id'] in ids]
        return {'passed':sum(effective(row)=='passed' for row in subset),'executed':len(subset),'required':len(ids),
                'accuracy':sum(effective(row)=='passed' for row in subset)/len(subset) if subset else None}
    stats['unanswerable_cases']=rate({8,9,10})
    stats['compliance_cases']=rate(set(range(21,26)))
    stats['pass_rate']=stats['passed']/len(rows) if rows else None
    stats['notice']='用量为供应商返回值（可能不完整），费用为预算预留；未运行项不进入已完成分母，发布仍要求全30项。'
    return stats


def stop(user,identifier):
    with db() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row=connection.execute('SELECT status FROM evaluation_jobs WHERE id=?',(identifier,)).fetchone()
        if not row:raise Problem('评测任务不存在',404)
        if row['status'] not in ('queued','running'):raise Problem('任务已结束，无需停止',409)
        connection.execute("UPDATE evaluation_jobs SET cancel_requested=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END,updated=? WHERE id=?",(now(),identifier))
        connection.execute('INSERT INTO evaluation_reviews VALUES(?,?,?,?,?,?,?)',(uuid.uuid4().hex,identifier,0,user['user_id'],'cancel','管理员请求停止',now()))
    return detail(identifier)


def review(user,identifier,case_id,data):
    if data.get('decision') not in ('passed','failed','needs_review') or type(data.get('revision')) is not int or not isinstance(data.get('reason'),str) or not 1<=len(data['reason'].strip())<=2000:raise Problem('请填写复核结论、当前修订号和1至2000字理由')
    with db() as connection:
        connection.execute('BEGIN IMMEDIATE')
        job=connection.execute('SELECT * FROM evaluation_jobs WHERE id=?',(identifier,)).fetchone()
        if not job:raise Problem('评测任务不存在',404)
        if job['owner']==user['user_id']:raise Problem('请由另一位具名管理员独立复核',403)
        if job['status'] not in ('completed','failed','cancelled','interrupted'):raise Problem('请等待任务结束后复核',409)
        result=connection.execute('SELECT * FROM evaluation_results WHERE job_id=? AND case_id=?',(identifier,case_id)).fetchone()
        if not result:raise Problem('评测结果不存在',404)
        if data['decision']=='passed' and json.loads(result['result'])['score']['auto_status']=='failed':raise Problem('自动检查失败不能人工覆盖为通过，请修复后重新评测',409)
        changed=connection.execute('UPDATE evaluation_results SET review=?,reason=?,reviewer=?,revision=revision+1 WHERE job_id=? AND case_id=? AND revision=?',(data['decision'],data['reason'].strip(),user['user_id'],identifier,case_id,data['revision']))
        if not changed.rowcount:raise Problem('结果已被其他人复核，请刷新',409)
        connection.execute('INSERT INTO evaluation_reviews VALUES(?,?,?,?,?,?,?)',(uuid.uuid4().hex,identifier,case_id,user['user_id'],data['decision'],data['reason'].strip(),now()))
    return detail(identifier)


def claim(worker):
    with db() as connection:
        connection.execute('BEGIN IMMEDIATE')
        # 崩溃任务不自动重跑，避免重复付费；历史结果保留，门禁阻断。
        connection.execute("UPDATE evaluation_jobs SET status='interrupted',error='评测执行器中断，请检查结果后新建任务',updated=? WHERE status='running' AND heartbeat<?",(now(),time.time()-45))
        row=connection.execute("SELECT id FROM evaluation_jobs WHERE status='queued' AND cancel_requested=0 ORDER BY created LIMIT 1").fetchone()
        if not row:return None
        connection.execute("UPDATE evaluation_jobs SET status='running',worker=?,heartbeat=?,updated=? WHERE id=?",(worker,time.time(),now(),row['id']))
        return row['id']


def owner_enabled(identifier):
    with db() as connection:
        row=connection.execute('SELECT owner FROM evaluation_jobs WHERE id=?',(identifier,)).fetchone()
    if not row:return False
    with connect() as connection:
        return bool(connection.execute('SELECT 1 FROM user_accounts WHERE user_id=? AND enabled=1',(row['owner'],)).fetchone())


def pulse(identifier,worker):
    with db() as connection:
        connection.execute("UPDATE evaluation_jobs SET heartbeat=?,updated=? WHERE id=? AND worker=? AND status='running'",(time.time(),now(),identifier,worker))
        row=connection.execute('SELECT status,cancel_requested FROM evaluation_jobs WHERE id=? AND worker=?',(identifier,worker)).fetchone()
    return not row or row['status']!='running' or bool(row['cancel_requested']) or not owner_enabled(identifier)


def save_result(identifier,worker,result):
    with db() as connection:
        connection.execute('BEGIN IMMEDIATE')
        if not connection.execute("SELECT 1 FROM evaluation_jobs WHERE id=? AND worker=? AND status='running'",(identifier,worker)).fetchone():raise Problem('评测任务所有权已失效',409)
        connection.execute('INSERT INTO evaluation_results(job_id,case_id,result) VALUES(?,?,?)',(identifier,result['case_id'],canonical(result)))


def finish(identifier,worker,status,error=''):
    with db() as connection:
        connection.execute("UPDATE evaluation_jobs SET status=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE ? END,error=?,updated=? WHERE id=? AND worker=? AND status='running'",(status,error,now(),identifier,worker))


def compare(left,right):
    a=detail(left);b=detail(right)
    same=a['snapshot']['suite']['hash']==b['snapshot']['suite']['hash'] and a['snapshot']['case_ids']==b['snapshot']['case_ids'] and a['snapshot']['scoring_version']==b['snapshot']['scoring_version'] and a['snapshot']['fixture_hash']==b['snapshot']['fixture_hash']
    first={r['case_id']:r for r in a['results']};second={r['case_id']:r for r in b['results']}
    return {'comparable':same,'notice':'仅相同题库、评分版本及用例范围可直接比较；模型、提示词与代码差异须结合明细判断。',
            'left':{'id':left,'summary':a['summary'],'gate':a['gate'],'version':a['snapshot']['code_version'],'model':a['snapshot']['model'],'prompt_revision':a['snapshot']['prompt']['revision'],'prompt_hash':hashlib.sha256(a['snapshot']['prompt']['content'].encode()).hexdigest()},
            'right':{'id':right,'summary':b['summary'],'gate':b['gate'],'version':b['snapshot']['code_version'],'model':b['snapshot']['model'],'prompt_revision':b['snapshot']['prompt']['revision'],'prompt_hash':hashlib.sha256(b['snapshot']['prompt']['content'].encode()).hexdigest()},
            'changes':[{'case_id':i,'left':effective(first[i]) if i in first else 'not_run','right':effective(second[i]) if i in second else 'not_run'} for i in sorted(set(first)|set(second))]}
