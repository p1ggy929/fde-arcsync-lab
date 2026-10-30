import json
import uuid
from ..core.store import connect, now, audit
from ..core.service import Problem
from .harness_store import user_key

def start_run(user,question):
    identifier=uuid.uuid4().hex;t=now()
    with connect() as db:
        db.execute('INSERT INTO agent_runs VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (identifier,user['role'],question,'running','','[]',t,t,'{}',''))
        db.execute('INSERT INTO agent_run_owners VALUES(?,?)',(identifier,user_key(user)))
        audit(db,user,'启动智能查阅',detail={'run_id':identifier})
    return identifier

def append_step(identifier,kind,detail):
    with connect() as db:
        db.execute('INSERT INTO agent_steps(run_id,at,kind,detail) VALUES(?,?,?,?)',(identifier,now(),kind,json.dumps(detail,ensure_ascii=False)))

def finish_run(user,identifier,status,answer,sources,usage,error=''):
    with connect() as db:
        db.execute('UPDATE agent_runs SET status=?,answer=?,sources=?,updated=?,usage=?,error=? WHERE id=?',
                   (status,answer,json.dumps(sources,ensure_ascii=False),now(),json.dumps(usage),error,identifier))
        audit(db,user,'结束智能查阅',detail={'run_id':identifier,'status':status,'sources':len(sources),'usage':usage})

def history(user):
    with connect() as db:
        rows=[dict(r) for r in db.execute('SELECT r.id,r.question,r.status,r.created,r.updated,r.error FROM agent_runs r JOIN agent_run_owners o ON o.run_id=r.id WHERE o.user_id=? ORDER BY r.created DESC LIMIT 20',(user_key(user),))]
    return rows

def detail(user,identifier):
    with connect() as db:
        row=db.execute('SELECT r.* FROM agent_runs r JOIN agent_run_owners o ON o.run_id=r.id WHERE r.id=? AND o.user_id=?',(identifier,user_key(user))).fetchone()
        if not row:raise Problem('记录不存在',404)
        result=dict(row)
        result['sources']=json.loads(result['sources']);result['usage']=json.loads(result['usage'])
        result['steps']=[{**dict(s),'detail':json.loads(s['detail'])} for s in db.execute('SELECT * FROM agent_steps WHERE run_id=? ORDER BY id',(identifier,))]
    # A stored answer may mention data that has since become restricted or obsolete.
    from ..data_models.knowledge import valid_sources
    from ..data_models.config import ModelSettings
    valid_sources(user,result['sources'],ModelSettings.load().allow_restricted)
    return result


def recover_interrupted():
    with connect() as db:
        db.execute("UPDATE agent_decision_logs SET status='interrupted',result_summary='服务中断，执行结果须核对' WHERE status='running' AND run_id IN (SELECT id FROM agent_harness_runs WHERE status='running')")
        db.execute("UPDATE agent_harness_runs SET status='interrupted',updated=? WHERE status='running'",(now(),))
        db.execute("UPDATE agent_runs SET status='interrupted',answer='',updated=?,error=? WHERE status='running'",
                   (now(),'上次服务中断，请重新提问'))
