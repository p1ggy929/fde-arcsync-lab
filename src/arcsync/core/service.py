"""Deterministic authorization and workflow rules. No model may bypass these checks."""
import json
from datetime import datetime, timezone
from .store import ROLES, audit, event, now

class Problem(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status

def require(user, roles):
    if user['role'] not in roles:
        raise Problem('当前岗位无权执行此操作',403)

def text(data,key,maximum=2000):
    value = str(data.get(key,'')).strip()
    if not value or len(value)>maximum:
        raise Problem(f'{key} 不能为空且不得超过 {maximum} 字')
    return value

def row(db,table,key):
    # Table names are internal constants, never user input.
    found = db.execute(f'SELECT * FROM {table} WHERE id=?',(key,)).fetchone()
    if not found:
        raise Problem('记录不存在',404)
    return dict(found)

def can_read(user, doc):
    return doc['level']=='公开' or user['role']=='protector'

def readable_document(db,user,identifier):
    found=db.execute('SELECT * FROM documents WHERE id=?',(identifier,)).fetchone()
    doc=dict(found) if found else None
    if not doc or not can_read(user,doc):
        raise Problem('档案不存在或不在授权范围内',404)
    return doc

def business(db, identifier):
    b=row(db,'businesses',identifier)
    b['citations']=json.loads(b['citations'])
    return b

def settings(db):
    return {r['key']:r['value'] for r in db.execute('SELECT * FROM settings')}

def check_active(db):
    if settings(db)['paused']=='true':
        raise Problem('试点已暂停，当前仅可查看、保存草稿和处理人工异常',409)

def check_owner(user,b):
    if b['owner']!=user['role']:
        raise Problem('请由当前责任岗位操作；交接须先由接手人确认',403)

def queue_handoff(db,user,b,receiver,note):
    if b['owner']==receiver:
        return
    if db.execute("SELECT 1 FROM handoffs WHERE business_id=? AND status='待接手'",(b['id'],)).fetchone():
        raise Problem('业务已有待接手交接，请先确认',409)
    db.execute('INSERT INTO handoffs(business_id,sender,receiver,note,status,created) VALUES(?,?,?,?,?,?)',
        (b['id'],b['owner'],receiver,note,'待接手',now()))
    event(db,b['id'],user,'发起交接',ROLES[receiver][1]+'：'+note)


def change(db,user,b,status,action,detail=''):

    db.execute('UPDATE businesses SET status=?,updated=?,revision=revision+1 WHERE id=?',(status,now(),b['id']))
    event(db,b['id'],user,action,detail)
    audit(db,user,action,b['id'],{'from':b['status'],'to':status,'detail':detail})

def visible_audit(db,user):
    if user['role'] not in ('protector','director'):
        return []
    logs=[dict(r) for r in db.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 200')]
    if user['role']=='director':
        for entry in logs:
            if entry['role']=='protector':
                entry['detail']=json.dumps({'notice':'专员操作细节仅限保护专员查看'},ensure_ascii=False)
    return logs


def state(db,user):
    docs=[dict(r) for r in db.execute('SELECT * FROM documents ORDER BY id') if can_read(user,r)]
    accessible={d['id'] for d in docs}
    bs=[business(db,r['id']) for r in db.execute('SELECT id FROM businesses ORDER BY created DESC,id')]
    issues=[dict(r) for r in db.execute('SELECT * FROM issues ORDER BY id DESC') if not r['document_id'] or r['document_id'] in accessible]
    s=settings(db)
    age=(datetime.now(timezone.utc)-datetime.fromisoformat(s['sync_at'])).total_seconds()
    s['stale']=age>3600 or s['sync_state']!='healthy'
    return {'user':user,'roles':[{'id':k,'name':v[0],'label':v[1]} for k,v in ROLES.items()],
      'documents':docs,'businesses':bs,'issues':issues,'settings':s,
      'handoffs':[dict(r) for r in db.execute('SELECT * FROM handoffs ORDER BY id DESC')],
      'feedback':[dict(r) for r in db.execute('SELECT * FROM feedback ORDER BY id DESC')],
      'audit':visible_audit(db,user),
      'events':[dict(r) for r in db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 500')],
      'demo':True,'clock':now()}

def mutate(db,user,action,data):
    role=user['role']
    if action=='create':
        require(user,('organizer','reviewer','representative'))
        check_active(db)
        title,applicant,purpose=text(data,'title',100),text(data,'applicant',100),text(data,'purpose',500)
        n=db.execute('SELECT COUNT(*) FROM businesses').fetchone()[0]+1
        bid=f'CY-{datetime.now(timezone.utc).strftime("%Y%m%d")}-{n:04d}'
        t=now()
        db.execute('INSERT INTO businesses(id,title,applicant,purpose,status,owner,created,updated) VALUES(?,?,?,?,?,?,?,?)',
            (bid,title,applicant,purpose,'待权限核验','reviewer',t,t))
        event(db,bid,user,'创建查阅申请',purpose)
        audit(db,user,'创建查阅申请',bid)
        return {'id':bid}
    if action=='pause':
        require(user,('protector','director'))
        reason=text(data,'reason',500)
        if not isinstance(data.get('paused'),bool): raise Problem('暂停状态必须是布尔值')
        paused=data['paused']
        if not paused:
            require(user,('protector',))
        db.execute('UPDATE settings SET value=? WHERE key=?',('true' if paused else 'false','paused'))
        audit(db,user,'暂停试点' if paused else '复盘后恢复试点',detail={'reason':reason})
        return {}
    if action=='sync':
        require(user,('engineer',))
        # Demo adapter refreshes only local fixtures; it never claims upstream synchronization.
        db.execute("UPDATE settings SET value=? WHERE key='sync_at'",(now(),))
        audit(db,user,'刷新本地合成数据快照',detail={'source':'local-fixtures','upstream_connected':False})
        return {}
    if action=='feedback':
        require(user,('representative','organizer','reviewer'))
        b=business(db,text(data,'business_id',100))
        version=text(data,'version',100)
        content=text(data,'content')
        db.execute('INSERT INTO feedback(business_id,version,content,status,created) VALUES(?,?,?,?,?)',(b['id'],version,content,'待核对',now()))
        audit(db,user,'受理服务反馈',b['id'],{'version':version})
        return {}
    if action=='reply':
        require(user,('reviewer',))
        f=row(db,'feedback',data.get('id'))
        if f['status']=='已核对': raise Problem('反馈已经核对',409)
        reply=text(data,'reply')
        db.execute("UPDATE feedback SET reply=?,status='已核对' WHERE id=?",(reply,f['id']))
        audit(db,user,'核对服务反馈',f['business_id'],{'feedback_id':f['id'],'reply':reply})
        return {}
    if action=='resolve':
        require(user,('organizer','reviewer','engineer','protector'))
        issue=row(db,'issues',data.get('id'))
        if issue['status']=='已处理': raise Problem('异常已经处理',409)
        if issue['owner']!=role: raise Problem('仅分派岗位可处理该异常',403)
        resolution=text(data,'resolution')
        db.execute("UPDATE issues SET status='已处理',resolution=? WHERE id=?",(resolution,issue['id']))
        # A repair note never silently marks the source archive as clean.
        b=business(db,issue['business_id'])
        left=db.execute("SELECT COUNT(*) FROM issues WHERE business_id=? AND status!='已处理'",(b['id'],)).fetchone()[0]
        if left==0:
            queue_handoff(db,user,b,'reviewer',resolution)
            change(db,user,b,'待复核','异常处理后交审核复核',resolution)
        audit(db,user,'处理异常',b['id'],{'issue_id':issue['id'],'resolution':resolution})
        return {}
    if action=='accept':
        h=row(db,'handoffs',data.get('id'))
        if h['receiver']!=role: raise Problem('仅指定接手岗位可确认',403)
        if h['status']!='待接手': raise Problem('该交接已处理',409)
        b=business(db,h['business_id'])
        if b['owner']!=h['sender'] or b['status']=='已交付': raise Problem('业务状态已变化，请重新交接',409)
        db.execute("UPDATE handoffs SET status='已接手',accepted=? WHERE id=?",(now(),h['id']))
        db.execute('UPDATE businesses SET owner=?,updated=?,revision=revision+1 WHERE id=?',(role,now(),b['id']))
        event(db,b['id'],user,'确认接手',h['note'])
        audit(db,user,'确认接手',b['id'],{'handoff_id':h['id']})
        return {}
    b=business(db,text(data,'business_id',100))
    if data.get('revision')!=b['revision']:
        raise Problem('记录已更新，请刷新后重试',409)
    if action=='issue':
        if b['status']=='已交付': raise Problem('已交付业务请通过服务反馈核对',409)
        require(user,('organizer','reviewer','engineer','protector'))
        kind,detail=text(data,'kind',100),text(data,'detail')
        db.execute("UPDATE handoffs SET status='因异常取消' WHERE business_id=? AND status='待接手'",(b['id'],))
        docid=data.get('document_id','')
        if docid:
            linked=readable_document(db,user,docid)
            if linked['level']!='公开':
                raise Problem('受限资料不得关联公开试点业务，请走原系统人工流程',403)
        db.execute('INSERT INTO issues(business_id,document_id,kind,detail,owner,status,created) VALUES(?,?,?,?,?,?,?)',
            (b['id'],docid,kind,detail,'reviewer','待分派',now()))
        change(db,user,b,'异常处理中','暂停建议并转人工',kind+'：'+detail)
        return {}
    if action=='assign':
        require(user,('reviewer',))
        issue=row(db,'issues',data.get('id'))
        if issue['business_id']!=b['id'] or issue['status']=='已处理': raise Problem('异常状态不允许分派',409)
        target=text(data,'receiver',30)
        if target not in ('organizer','reviewer','engineer','protector'): raise Problem('无效处理岗位')
        reason=text(data,'note',500)
        db.execute("UPDATE issues SET owner=?,status='待处理' WHERE id=?",(target,issue['id']))
        # Assignment is explicit and logged; handoff ownership still requires acceptance.
        event(db,b['id'],user,'分派异常',ROLES[target][1]+'：'+reason)
        audit(db,user,'分派异常',b['id'],{'issue_id':issue['id'],'receiver':target,'reason':reason})
        return {}
    check_owner(user,b)
    if action=='note':
        if b['status']=='已交付': raise Problem('已交付记录不可修改，请登记服务反馈',409)
        note=str(data.get('note','')).strip()
        if len(note)>4000: raise Problem('草稿不得超过 4000 字')
        db.execute('UPDATE businesses SET note=?,updated=?,revision=revision+1 WHERE id=?',(note,now(),b['id']))
        audit(db,user,'保存随单草稿',b['id'])
        return {}
    if action=='handoff':
        require(user,('organizer','reviewer','engineer','protector','representative'))
        if b['status']=='已交付': raise Problem('已交付业务不可交接',409)
        receiver=text(data,'receiver',40)
        if receiver not in ROLES or receiver==role: raise Problem('请选择其他接手岗位')
        if b['status'] in ('待权限核验','待审核','待复核') and receiver!='reviewer':
            raise Problem('当前审核节点须由审核员负责',409)
        if b['status']=='定位中' and receiver!='organizer': raise Problem('定位节点须由整理员负责',409)
        note=text(data,'note')
        if db.execute("SELECT 1 FROM handoffs WHERE business_id=? AND status='待接手'",(b['id'],)).fetchone():
            raise Problem('已有待接手交接，请等待确认',409)
        db.execute('INSERT INTO handoffs(business_id,sender,receiver,note,status,created) VALUES(?,?,?,?,?,?)',
            (b['id'],role,receiver,note,'待接手',now()))
        event(db,b['id'],user,'发起交接',ROLES[receiver][1]+'：'+note)
        audit(db,user,'发起交接',b['id'],{'receiver':receiver,'version':b['revision'],'unresolved':note})
        return {}
    check_active(db)
    if db.execute("SELECT 1 FROM handoffs WHERE business_id=? AND status='待接手'",(b['id'],)).fetchone():
        raise Problem('请等待接手人确认交接后再推进业务',409)
    if action=='authorize':
        require(user,('reviewer',))
        if b['status']!='待权限核验': raise Problem('当前状态不能核验权限',409)
        scope=text(data,'scope',500)
        if scope!='公开档案试点': raise Problem('演示仅支持公开档案试点授权')
        queue_handoff(db,user,b,'organizer',scope)
        change(db,user,b,'定位中','核验公开档案试点权限',scope)
    elif action=='cite':
        require(user,('organizer',))
        if b['status']!='定位中': raise Problem('当前业务尚未进入定位环节',409)
        doc=readable_document(db,user,text(data,'document_id',100))
        if doc['status']!='有效': raise Problem('该资料存在版本或数据质量问题，请先转人工',409)
        if data.get('version')!=doc['version']: raise Problem('资料版本已变化，请重新核对',409)
        page=data.get('scan_page')
        if isinstance(page,bool) or not isinstance(page,int) or not 1<=page<=doc['pages']: raise Problem('扫描页超出有效范围')
        paper=doc['paper_start']+page-1
        if type(data.get('paper_page')) is not int or data.get('paper_page')!=paper: raise Problem('纸质页码与已校验映射不一致')
        if doc['correction'] and data.get('correction_checked') is not True: raise Problem('请先核对后续更正关系',409)
        item={'document_id':doc['id'],'version':doc['version'],'scan_page':page,'paper_page':paper,'confirmed':True,'correction_checked':data.get('correction_checked') is True,'by':user['name'],'at':now()}
        if any(c['document_id']==doc['id'] and c['scan_page']==page for c in b['citations']): raise Problem('该卷页已经加入依据',409)
        db.execute('UPDATE businesses SET citations=? WHERE id=?',(json.dumps(b['citations']+[item],ensure_ascii=False),b['id']))
        change(db,user,b,b['status'],'人工确认卷页引用',f"{doc['id']} · {doc['version']} · 扫描第 {page} 页 → 纸质第 {paper} 页；已人工核对。")
    elif action=='submit':
        require(user,('organizer',))
        if b['status']!='定位中' or not b['citations']: raise Problem('请先完成人工卷页确认',409)
        if db.execute("SELECT 1 FROM issues WHERE business_id=? AND status!='已处理'",(b['id'],)).fetchone(): raise Problem('存在未解决异常',409)
        note=text(data,'note')
        if db.execute("SELECT 1 FROM handoffs WHERE business_id=? AND status='待接手'",(b['id'],)).fetchone(): raise Problem('存在未接手交接',409)
        change(db,user,b,'待审核','提交审核',note)
        db.execute('INSERT INTO handoffs(business_id,sender,receiver,note,status,created) VALUES(?,?,?,?,?,?)',(b['id'],role,'reviewer',note,'待接手',now()))
    elif action in ('approve','return','deliver','resume'):
        require(user,('reviewer',))
        if action in ('approve','return') and b['status'] not in ('待审核','待复核'): raise Problem('当前状态不能审核',409)
        if action=='approve':
            if not b['citations']: raise Problem('缺少已确认引用，须退回定位',409)
            for c in b['citations']:
                d=readable_document(db,user,c['document_id'])
                if d['version']!=c['version'] or d['status']!='有效': raise Problem('依据资料已变化，须退回复核',409)
            if db.execute("SELECT 1 FROM issues WHERE business_id=? AND status!='已处理'",(b['id'],)).fetchone(): raise Problem('仍有未解决异常',409)
            change(db,user,b,'待交付','审核通过',text(data,'note'))
        elif action=='return':
            reason=text(data,'reason')
            queue_handoff(db,user,b,'organizer',reason)
            db.execute("UPDATE businesses SET return_reason=?,citations='[]' WHERE id=?",(reason,b['id']))
            change(db,user,b,'定位中','退回定位',reason)
        elif action=='deliver':
            if b['status']!='待交付': raise Problem('须审核通过后才能交付',409)
            change(db,user,b,'已交付','记录人工交付',text(data,'note'))
        elif action=='resume':
            if b['status']!='待复核': raise Problem('仅异常复核完成后可恢复',409)
            if db.execute("SELECT 1 FROM issues WHERE business_id=? AND status!='已处理'",(b['id'],)).fetchone(): raise Problem('仍有未解决异常',409)
            note=text(data,'note')
            queue_handoff(db,user,b,'organizer',note)
            change(db,user,b,'定位中','复核后恢复定位',note)
    else:
        raise Problem('未知操作',404)
    return {}
