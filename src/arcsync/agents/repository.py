import json
import uuid
from ..core.store import connect, now, audit
from ..core.service import Problem

def start_run(user,question):
    identifier=uuid.uuid4().hex;t=now()
    with connect() as db:
        db.execute('INSERT INTO agent_runs VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (identifier,user['role'],question,'running','','[]',t,t,'{}',''))
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
        rows=[dict(r) for r in db.execute('SELECT id,question,status,created,updated,error FROM agent_runs WHERE role=? ORDER BY created DESC LIMIT 20',(user['role'],))]
    return rows

def detail(user,identifier):
    with connect() as db:
        row=db.execute('SELECT * FROM agent_runs WHERE id=? AND role=?',(identifier,user['role'])).fetchone()
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
        db.execute("UPDATE agent_runs SET status='interrupted',answer='',updated=?,error=? WHERE status='running'",
                   (now(),'上次服务中断，请重新提问'))
