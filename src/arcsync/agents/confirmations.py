"""Interrupt before create_access_draft; explicit, version-bound employee confirmation."""
import json
import time
import uuid
from ..core.store import connect, now, audit
from ..core.service import Problem, require, check_active
from . import access, harness_store


def preview_for(case,snapshot):
    return {'case_id':case['id'],'revision':case['revision'],
            'title':case['content']['title'],'applicant':case['content']['applicant'],
            'purpose':case['content']['purpose'],'usage':case['content']['usage'],
            'archives':[{'document_id':ref['document_id'],'title':snap['document']['title'],
                         'version':ref['version'],'scan_page':ref['scan_page'],
                         'paper_page':next(c['paper_page'] for c in snap['chunks'] if c['scan_page']==ref['scan_page'])}
                        for ref,snap in zip(case['content']['archives'],snapshot['archives'])],
            'notice':'确认后仅保存查阅申请草稿，不提交申请、不审批、不替代人工卷页核对。'}


def text(preview):
    return '\n'.join(['待确认查阅申请草稿（尚未创建）','查阅事项：'+preview['title'],'申请单位：'+preview['applicant'],
                      '查阅目的：'+preview['purpose'],'利用方式：'+preview['usage'],'候选档案：',
                      *[f"• {ref['title']} / {ref['document_id']} / {ref['version']} / 扫描第{ref['scan_page']}页 → 纸质第{ref['paper_page']}页" for ref in preview['archives']],preview['notice']])


def public(row):
    return {'id':row['id'],'run_id':row['run_id'],'round_no':row['round_no'],
            'status':row['status'],'preview':json.loads(row['preview_json']),
            'preview_hash':row['preview_hash'],'expires_at':row['expires'],
            'session_id':row['session_id'],'message_id':row['message_id']}


def prepare(user,identifier,revision,token,context):
    require(user,('organizer','reviewer','representative'))
    with connect() as db:
        db.execute('BEGIN IMMEDIATE');check_active(db)
        case=access.valid_receipt(db,user,identifier,revision,token)
        snapshot,_=access.snapshot_case(db,user,case);preview=preview_for(case,snapshot)
        receipt=db.execute('SELECT expires FROM access_validations WHERE id=?',(token,)).fetchone()
        run_id=context.get('run_id') or uuid.uuid4().hex;round_no=context.get('round_no',1)
        existing=db.execute('SELECT * FROM agent_confirmations WHERE run_id=? AND case_id=? AND revision=? AND user_id=?',(run_id,identifier,revision,access.owner(user))).fetchone()
        if existing:return public(existing)
        confirmation_id=uuid.uuid4().hex
        db.execute('INSERT INTO agent_confirmations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (confirmation_id,run_id,access.owner(user),round_no,identifier,revision,token,json.dumps(preview,ensure_ascii=False),access.fingerprint(preview),'waiting','{}',context.get('session_id'),context.get('message_id'),now(),receipt['expires']))
        harness_store.state(run_id,'waiting_confirmation',round_no,db)
        audit(db,user,'智能体暂停等待草稿确认',detail={'confirmation_id':confirmation_id,'case_id':identifier,'revision':revision})
        return public(db.execute('SELECT * FROM agent_confirmations WHERE id=?',(confirmation_id,)).fetchone())


def _resolve(user,identifier,preview_hash,approved,expected=None,already_confirmed=False):
    started=time.perf_counter()
    if type(approved) is not bool:raise Problem('确认决定须为布尔值')
    require(user,('organizer','reviewer','representative'))
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM agent_confirmations WHERE id=? AND user_id=?',(identifier,access.owner(user))).fetchone()
        if not row:raise Problem('待确认操作不存在',404)
        if expected and tuple(expected)!=(row['case_id'],row['revision'],row['validation_id']):raise Problem('确认内容与建单参数不一致',409)
        if already_confirmed and row['status']!='completed':raise Problem('员工尚未确认这份具体草稿',403)
        if not already_confirmed and row['preview_hash']!=preview_hash:raise Problem('确认内容校验失败，请刷新草稿预览',409)
        if row['status'] in ('completed','cancelled'):
            result=json.loads(row['result_json'])
            if approved!=(row['status']=='completed'):raise Problem('该确认已结束，不能更改决定',409)
            return result
        if row['status']!='waiting':raise Problem('确认已失效，请重新校验',409)
        if row['expires']<=now() and approved:raise Problem('确认已过期，请重新校验并生成预览',409)
        arguments={'case_id':row['case_id'],'revision':row['revision'],'confirmation_id':identifier}
        log_id=harness_store.begin(user,row['run_id'],row['round_no'],'resume','create_access_draft',arguments,identifier,db)
        if not approved:
            result={'status':'cancelled','confirmation_id':identifier,'run_id':row['run_id'],'message':'已取消，未创建查阅申请草稿'}
            state='cancelled'
        else:
            check_active(db)
            case=access.valid_receipt(db,user,row['case_id'],row['revision'],row['validation_id'])
            snapshot,_=access.snapshot_case(db,user,case)
            if access.fingerprint(preview_for(case,snapshot))!=row['preview_hash']:raise Problem('草稿内容或档案已变化，请重新校验',409)
            draft=access._create_confirmed(db,user,row['case_id'],row['revision'],row['validation_id'])
            result={'status':'completed','confirmation_id':identifier,'run_id':row['run_id'],'draft':draft,'message':'已确认并保存查阅申请草稿，尚未提交权限核验'}
            state='completed'
        db.execute('UPDATE agent_confirmations SET status=?,result_json=? WHERE id=?',(state,json.dumps(result,ensure_ascii=False),identifier))
        harness_store.state(row['run_id'],state,db=db)
        db.execute("UPDATE agent_runs SET status=?,answer=?,updated=? WHERE id=? AND status='waiting_confirmation'",(state,result['message'],now(),row['run_id']))
        harness_store.finish_log(log_id,state,(time.perf_counter()-started)*1000,result,db)
        audit(db,user,'确认创建查阅草稿' if approved else '取消创建查阅草稿',detail={'confirmation_id':identifier,'run_id':row['run_id']})
        return result


def list_for(user,session_id=None):
    with connect() as db:
        rows=db.execute('SELECT * FROM agent_confirmations WHERE user_id=? AND (? IS NULL OR session_id=?) ORDER BY created DESC LIMIT 50',(access.owner(user),session_id,session_id)).fetchall()
        items=[]
        for row in rows:
            item=public(row)
            if row['status']=='waiting':
                try:
                    if row['expires']<=now():raise Problem('确认已过期',409)
                    access.valid_receipt(db,user,row['case_id'],row['revision'],row['validation_id'])
                except Problem:
                    item.update(status='stale',preview=None)
            if row['status'] in ('completed','cancelled'):
                item['result']=json.loads(row['result_json'])
                item['preview']=None
            items.append(item)
        return items


def resolve(user,identifier,preview_hash,approved,expected=None,already_confirmed=False):
    started=time.perf_counter()
    try:return _resolve(user,identifier,preview_hash,approved,expected,already_confirmed)
    except Problem as error:
        with connect() as db:
            row=db.execute('SELECT run_id,round_no FROM agent_confirmations WHERE id=? AND user_id=?',(identifier,access.owner(user))).fetchone()
        if row:
            log=harness_store.begin(user,row['run_id'],row['round_no'],'resume','create_access_draft',{'confirmation_id':identifier,'approved':approved})
            harness_store.finish_log(log,'rejected',(time.perf_counter()-started)*1000,{'error':error.message,'code':error.status*100+1})
        raise
