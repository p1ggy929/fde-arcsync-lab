"""Owned access cases and atomic draft creation. Models cannot supply check outcomes."""
import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from ..core.store import connect, now, audit
from ..core.service import Problem, check_active, require, can_read, mutate


def initialize(db):
    mysql=getattr(db,'dialect','sqlite')=='mysql'
    key='VARCHAR(120)' if mysql else 'TEXT'
    long='LONGTEXT' if mysql else 'TEXT'
    for sql in [
        f'CREATE TABLE IF NOT EXISTS access_cases(id {key} PRIMARY KEY,user_id {key} NOT NULL,revision INTEGER NOT NULL,content {long} NOT NULL,updated {key} NOT NULL)',
        f'CREATE TABLE IF NOT EXISTS access_validations(id {key} PRIMARY KEY,case_id {key} NOT NULL,revision INTEGER NOT NULL,user_id {key} NOT NULL,snapshot {long} NOT NULL,expires {key} NOT NULL)',
        f'CREATE TABLE IF NOT EXISTS access_drafts(id {key} PRIMARY KEY,case_id {key} NOT NULL,revision INTEGER NOT NULL,user_id {key} NOT NULL,validation_id {key} NOT NULL,created {key} NOT NULL,business_id {key},UNIQUE(case_id,revision))',
        f'CREATE TABLE IF NOT EXISTS business_requesters(business_id {key} PRIMARY KEY,user_id {key} NOT NULL)',
        f'CREATE TABLE IF NOT EXISTS agent_tool_audit(id {key} PRIMARY KEY,run_id {key} NOT NULL,user_id {key} NOT NULL,tool {key} NOT NULL,arguments_json {long} NOT NULL,result_json {long} NOT NULL,created {key} NOT NULL)',
    ]:db.execute(sql+(' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin' if mysql else ''))


def owner(user):
    if not user.get('user_id'):raise Problem('请重新登录后操作查阅事项',401)
    return user['user_id']


def owned(db,user,identifier):
    row=db.execute('SELECT * FROM access_cases WHERE id=? AND user_id=?',(identifier,owner(user))).fetchone()
    if not row:raise Problem('查阅事项不存在',404)
    return {**dict(row),'content':json.loads(row['content'])}


def view(user,identifier=None):
    with connect() as db:
        if identifier:return owned(db,user,identifier)
        return {'cases':[{**dict(r),'content':json.loads(r['content'])} for r in db.execute('SELECT * FROM access_cases WHERE user_id=? ORDER BY updated DESC LIMIT 20',(owner(user),))]}


def update(user,identifier,revision,content):
    require(user,('organizer','reviewer','representative'))
    with connect() as db:
        db.execute('BEGIN IMMEDIATE');check_active(db)
        # Check every chosen archive before saving: no arbitrary restricted catalog IDs.
        for ref in content['archives']:
            doc=db.execute('SELECT * FROM documents WHERE id=?',(ref['document_id'],)).fetchone()
            if not doc or not can_read(user,doc):raise Problem('档案不存在或不在授权范围内',404)
        if identifier:
            case=owned(db,user,identifier)
            if case['revision']!=revision:raise Problem('查阅事项已变化，请重新读取',409)
            revision+=1
            db.execute('UPDATE access_cases SET revision=?,content=?,updated=? WHERE id=?',(revision,json.dumps(content,ensure_ascii=False),now(),identifier))
        else:
            if revision!=0:raise Problem('新事项版本须为0')
            identifier=uuid.uuid4().hex;revision=1
            db.execute('INSERT INTO access_cases VALUES(?,?,?,?,?)',(identifier,owner(user),revision,json.dumps(content,ensure_ascii=False),now()))
        audit(db,user,'更新查阅事项',detail={'case_id':identifier,'revision':revision})
    return view(user,identifier)


def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def archive_snapshot(db,user,identifier,version,seen=None):
    seen=set() if seen is None else set(seen)
    if identifier in seen or len(seen)>=8:raise Problem('更正关系存在循环或过长，须人工核对',409)
    seen.add(identifier)
    doc=db.execute('SELECT * FROM documents WHERE id=?',(identifier,)).fetchone()
    if not doc or not can_read(user,doc):raise Problem('档案不存在或不在授权范围内',404)
    doc=dict(doc)
    if version and doc['version']!=version:raise Problem('档案版本已变化，请重新检索',409)
    chunks=[dict(r) for r in db.execute('SELECT * FROM document_chunks WHERE document_id=? AND version=? ORDER BY scan_page,id',(identifier,doc['version']))]
    result={'document':doc,'chunks':chunks}
    if doc['correction']:result['correction']=archive_snapshot(db,user,doc['correction'],None,seen)
    return result


def inspect(user,identifier,version):
    with connect() as db:
        snapshot=archive_snapshot(db,user,identifier,version)
    def metadata(node):
        d=node['document'];result={k:d[k] for k in ('id','title','version','status','level','correction')}
        if 'correction' in node:result['next_correction']=metadata(node['correction'])
        return result
    return snapshot,metadata(snapshot)


def snapshot_case(db,user,case):
    content=case['content'];issues=[];snapshots=[]
    for field in ('title','applicant','purpose','usage'):
        if not content.get(field):issues.append({'field':field,'message':'请补齐'+{'title':'查阅事项','applicant':'申请单位','purpose':'查阅目的','usage':'利用方式'}[field]})
    if not content['archives']:issues.append({'field':'archives','message':'请选择档案及扫描页码'})
    for ref in content['archives']:
        snap=archive_snapshot(db,user,ref['document_id'],ref['version']);doc=snap['document']
        if doc['status']!='有效' or doc['level']!='公开':issues.append({'field':'archives','message':'仅有效公开档案可进入当前试点草稿，其他情况转人工核验'})
        def check_node(node):
            d=node['document']
            if d['status']!='有效' or d['level']!='公开' or not node['chunks']:issues.append({'field':'archives','message':'正文缺失、更正资料不可用或质量异常，须人工核对'})
            if 'correction' in node:check_node(node['correction'])
        check_node(snap)
        rows=[c for c in snap['chunks'] if c['scan_page']==ref['scan_page']]
        if len(rows)!=1 or not rows[0]['content'].strip() or rows[0]['paper_page']!=doc['paper_start']+ref['scan_page']-1:
            issues.append({'field':'scan_page','message':'页文本缺失或页码映射有歧义，须人工核对'})
        snapshots.append(snap)
    return {'case_id':case['id'],'revision':case['revision'],'content':content,'archives':snapshots},issues


def validate(user,identifier,revision,reads,checks):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE');check_active(db);case=owned(db,user,identifier)
        if case['revision']!=revision:raise Problem('查阅事项已变化，请重新读取',409)
        snapshot,issues=snapshot_case(db,user,case)
        for ref,snap in zip(case['content']['archives'],snapshot['archives']):
            digest=fingerprint(snap)
            if checks.get(ref['document_id'])!=digest:issues.append({'field':'validity','message':'须先调用 check_archive_validity 核对当前版本及更正关系'})
            if reads.get((ref['document_id'],ref['scan_page']))!=digest:issues.append({'field':'page','message':'须先调用 read_archive_page 读取当前页文本'})
            def correction_reads(node):
                if 'correction' not in node:return
                child=node['correction'];d=child['document'];expected=fingerprint(child)
                if not any(doc==d['id'] and value==expected for (doc,page),value in reads.items()):issues.append({'field':'correction','message':'须读取后续更正文件正文'})
                correction_reads(child)
            correction_reads(snap)
        if issues:
            audit(db,user,'查阅申请校验未通过',detail={'case_id':identifier,'issues':issues})
            return {'status':'needs_review' if any(i['field'] in ('archives','scan_page') for i in issues) else 'needs_input','issues':issues,'validation_id':None}
        token=secrets.token_urlsafe(32);expires=(datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat()
        db.execute('INSERT INTO access_validations VALUES(?,?,?,?,?,?)',(token,identifier,revision,owner(user),json.dumps(snapshot,ensure_ascii=False),expires))
        audit(db,user,'查阅申请校验通过',detail={'case_id':identifier,'revision':revision})
        return {'status':'passed','issues':[],'validation_id':token,'expires_at':expires,'notice':'仅允许创建草稿，不代表人工确认或开放审批通过'}


def valid_receipt(db,user,identifier,revision,token):
    case=owned(db,user,identifier)
    if case['revision']!=revision:raise Problem('事项已修改，请重新校验',409)
    receipt=db.execute('SELECT * FROM access_validations WHERE id=? AND case_id=? AND revision=? AND user_id=?',(token,identifier,revision,owner(user))).fetchone()
    if not receipt or receipt['expires']<=now():raise Problem('校验凭证无效或已过期，请重新校验',409)
    snapshot,issues=snapshot_case(db,user,case)
    if issues or fingerprint(snapshot)!=fingerprint(json.loads(receipt['snapshot'])):raise Problem('档案或页码已变化，请重新校验',409)
    return case


def can_create(user):
    with connect() as db:
        rows=db.execute('SELECT * FROM access_validations WHERE user_id=? AND expires>?',(owner(user),now())).fetchall()
        for row in rows:
            try:valid_receipt(db,user,row['case_id'],row['revision'],row['id']);return True
            except Problem:continue
    return False


def create(user,identifier,revision,token,confirmation_id):
    """Only a server-confirmed checkpoint may reach the database write."""
    from .confirmations import resolve
    if not isinstance(confirmation_id,str):raise Problem('创建草稿须先确认具体内容',403)
    return resolve(user,confirmation_id,None,True,expected=(identifier,revision,token),already_confirmed=True)


def _create_confirmed(db,user,identifier,revision,token):
    # Internal transaction-only primitive; never exposed as an HTTP/model tool.
    require(user,('organizer','reviewer','representative'))
    check_active(db);valid_receipt(db,user,identifier,revision,token)
    row=db.execute('SELECT * FROM access_drafts WHERE case_id=? AND revision=? AND user_id=?',(identifier,revision,owner(user))).fetchone()
    if row:
        if not row['business_id']:
            db.execute('UPDATE access_drafts SET validation_id=? WHERE id=?',(token,row['id']))
        return {**dict(row),'validation_id':token,'status':'submitted' if row['business_id'] else 'draft','url':'/access-drafts'}
    draft=uuid.uuid4().hex
    db.execute('INSERT INTO access_drafts VALUES(?,?,?,?,?,?,?)',(draft,identifier,revision,owner(user),token,now(),None))
    audit(db,user,'智能体创建查阅草稿',detail={'draft_id':draft,'case_id':identifier,'revision':revision})
    return {'id':draft,'case_id':identifier,'revision':revision,'status':'draft','url':'/access-drafts','notice':'尚未提交，须人工确认后提交权限核验'}


def drafts(user):
    with connect() as db:
        return [{**{k:r[k] for k in r.keys() if k!='snapshot'},'content':json.loads(r['snapshot'])['content']} for r in db.execute('SELECT d.*,v.snapshot,c.revision AS current_revision FROM access_drafts d JOIN access_cases c ON c.id=d.case_id JOIN access_validations v ON v.id=d.validation_id WHERE d.user_id=? ORDER BY d.created DESC LIMIT 100',(owner(user),))]


def submit(user,identifier):
    require(user,('organizer','reviewer','representative'))
    with connect() as db:
        db.execute('BEGIN IMMEDIATE');check_active(db)
        row=db.execute('SELECT * FROM access_drafts WHERE id=? AND user_id=?',(identifier,owner(user))).fetchone()
        if not row:raise Problem('草稿不存在',404)
        if row['business_id']:return {'id':row['business_id']}
        case=valid_receipt(db,user,row['case_id'],row['revision'],row['validation_id'])
        result=mutate(db,user,'create',case['content'])
        db.execute('UPDATE access_drafts SET business_id=? WHERE id=?',(result['id'],identifier))
        db.execute('UPDATE businesses SET note=? WHERE id=?',('智能体候选范围（未经人工卷页确认）：\n利用方式：'+case['content']['usage']+'\n'+'\n'.join(ref['document_id']+' / '+ref['version']+' / 扫描第 '+str(ref['scan_page'])+' 页' for ref in case['content']['archives']),result['id']))
        audit(db,user,'人工提交智能体查阅草稿',result['id'],{'draft_id':identifier})
        return result


def business_visible(db,user,row):
    if user['role'] not in ('organizer','representative'):return True
    if row['sample'] and row['owner']==user['role']:return True
    return bool(db.execute('SELECT 1 FROM business_requesters WHERE business_id=? AND user_id=?',(row['id'],owner(user))).fetchone())


def tool_audit(user,run_id,name,arguments,result):
    with connect() as db:
        db.execute('INSERT INTO agent_tool_audit VALUES(?,?,?,?,?,?,?)',(uuid.uuid4().hex,run_id,user.get('user_id') or 'legacy:'+user['role'],name,json.dumps(arguments,ensure_ascii=False),json.dumps(result,ensure_ascii=False),now()))
