"""Durable execution metadata and compact, queryable round/tool logs."""
import json
import uuid
from ..core.store import connect, now

MAX_ROUNDS=5


def initialize(db):
    mysql=getattr(db,'dialect','sqlite')=='mysql'
    key='VARCHAR(120)' if mysql else 'TEXT';long='LONGTEXT' if mysql else 'TEXT'
    for statement in [
        f'CREATE TABLE IF NOT EXISTS agent_run_owners(run_id {key} PRIMARY KEY,user_id {key} NOT NULL)',
        f'CREATE TABLE IF NOT EXISTS agent_harness_runs(id {key} PRIMARY KEY,user_id {key} NOT NULL,status {key} NOT NULL,round_count INTEGER NOT NULL,max_rounds INTEGER NOT NULL,session_id {key},message_id {key},created {key} NOT NULL,updated {key} NOT NULL)',
        f'CREATE TABLE IF NOT EXISTS agent_decision_logs(id {key} PRIMARY KEY,run_id {key} NOT NULL,user_id {key} NOT NULL,round_no INTEGER NOT NULL,kind {key} NOT NULL,call_id {key} NOT NULL,tool_name {key} NOT NULL,arguments_json {long} NOT NULL,duration_ms INTEGER NOT NULL,status {key} NOT NULL,result_summary {long} NOT NULL,created {key} NOT NULL)',
        f'CREATE TABLE IF NOT EXISTS agent_confirmations(id {key} PRIMARY KEY,run_id {key} NOT NULL,user_id {key} NOT NULL,round_no INTEGER NOT NULL,case_id {key} NOT NULL,revision INTEGER NOT NULL,validation_id {key} NOT NULL,preview_json {long} NOT NULL,preview_hash {key} NOT NULL,status {key} NOT NULL,result_json {long} NOT NULL,session_id {key},message_id {key},created {key} NOT NULL,expires {key} NOT NULL,UNIQUE(run_id,case_id,revision))',
    ]:db.execute(statement+(' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin' if mysql else ''))


def user_key(user):return user.get('user_id') or 'legacy:'+user['role']


def start(user,identifier,budget,interaction):
    with connect() as db:
        db.execute('INSERT INTO agent_harness_runs VALUES(?,?,?,?,?,?,?,?,?)',(identifier,user_key(user),'running',0,min(MAX_ROUNDS,budget),interaction.get('session_id'),interaction.get('message_id'),now(),now()))


def state(identifier,status,round_no=None,db=None):
    def write(connection):
        if round_no is None:connection.execute('UPDATE agent_harness_runs SET status=?,updated=? WHERE id=?',(status,now(),identifier))
        else:connection.execute('UPDATE agent_harness_runs SET status=?,round_count=?,updated=? WHERE id=?',(status,round_no,now(),identifier))
    if db is not None:write(db)
    else:
        with connect() as connection:write(connection)


def begin(user,identifier,round_no,kind,tool_name='',arguments=None,call_id='',db=None):
    key=uuid.uuid4().hex
    def write(connection):connection.execute('INSERT INTO agent_decision_logs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(key,identifier,user_key(user),round_no,kind,call_id[:120],tool_name[:120],json.dumps(arguments or {},ensure_ascii=False),0,'running','',now()))
    if db is not None:write(db)
    else:
        with connect() as connection:write(connection)
    return key


def summary(result):
    # Stage-5 summaries exclude full archive text, validation secrets and model reasoning.
    if isinstance(result,dict):
        safe={k:result[k] for k in ('status','code','error','message','notice','id','case_id','revision') if k in result}
        if result.get('issues'):safe['issues']=result['issues']
        if 'sources' in result:safe['source_count']=len(result['sources'])
        if 'source' in result:safe['source_count']=1
        if 'confirmation' in result:safe['confirmation_id']=result['confirmation']['id']
        if not safe:safe={'fields':list(result)[:12]}
        value=json.dumps(safe,ensure_ascii=False)
    else:value=str(result)
    return value[:800]


def finish_log(identifier,status,duration_ms,result,db=None):
    def write(connection):connection.execute('UPDATE agent_decision_logs SET status=?,duration_ms=?,result_summary=? WHERE id=?',(status,max(0,int(duration_ms)),summary(result),identifier))
    if db is not None:write(db)
    else:
        with connect() as connection:write(connection)


def logs(employee='',run_id='',tool='',start_at='',end_at='',offset=0):
    with connect() as db:
        return [dict(r) for r in db.execute('''SELECT * FROM agent_decision_logs WHERE (?='' OR user_id=?) AND (?='' OR run_id=?) AND (?='' OR tool_name=?) AND (?='' OR created>=?) AND (?='' OR created<=?) ORDER BY created DESC,id DESC LIMIT 51 OFFSET ?''',(employee,employee,run_id,run_id,tool,tool,start_at,start_at,end_at,end_at,offset))]
