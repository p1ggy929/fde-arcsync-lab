"""Authorized, versioned chunks; imported page text never overwrites older versions."""
import hashlib
import json
from ..core.store import connect, now, audit
from ..core.service import Problem, can_read, require, text

def available_chunks(user,allow_restricted=False):
    with connect() as db:
        docs=[dict(d) for d in db.execute('SELECT * FROM documents ORDER BY id')
              if can_read(user,d) and d['status']=='有效' and (d['level']=='公开' or allow_restricted)]
        chunks=[]
        for doc in docs:
            rows=[dict(r) for r in db.execute('SELECT * FROM document_chunks WHERE document_id=? AND version=? ORDER BY scan_page',(doc['id'],doc['version']))]
            # Existing fixtures have catalog summaries, not OCR. Label the distinction.
            if not rows:
                rows=[{'id':doc['id']+':'+doc['version']+':catalog','document_id':doc['id'],'version':doc['version'],
                       'scan_page':None,'paper_page':None,'content':doc['summary'],'source':'catalog'}]
            for row in rows:
                row.update(title=doc['title'],level=doc['level'],category=doc['category'],year=doc['year'],correction=doc['correction'])
                chunks.append(row)
        return chunks

def import_document(user,data):
    require(user,('engineer','protector'))
    identifier=text(data,'id',100);version=text(data,'version',40);title=text(data,'title',200)
    category=text(data,'category',60);level=data.get('level','公开')
    if level not in ('公开','受限'):raise Problem('开放级别必须为公开或受限')
    if level=='受限':require(user,('protector',))
    pages=data.get('pages');year=data.get('year')
    if not isinstance(year,int) or isinstance(year,bool) or not 1000<=year<=2100:raise Problem('年度无效')
    if not isinstance(pages,list) or not 1<=len(pages)<=20:raise Problem('每次导入须提供 1–20 页文本')
    checked=[];seen=set()
    for page in pages:
        if not isinstance(page,dict):raise Problem('页记录无效')
        scan=page.get('scan_page');paper=page.get('paper_page')
        if not isinstance(scan,int) or isinstance(scan,bool) or not 1<=scan<=5000 or scan in seen:raise Problem('扫描页无效或重复')
        if not isinstance(paper,int) or isinstance(paper,bool) or not 1<=paper<=100000:raise Problem('纸质页码无效')
        seen.add(scan);content=text(page,'text',5000)
        checked.append((scan,paper,content))
    # Existing business citation mapping assumes a constant paper page offset.
    offsets={paper-scan+1 for scan,paper,_ in checked}
    if len(offsets)!=1 or min(offsets)<1:raise Problem('当前业务只支持固定页码偏移，请先人工整理页码映射')
    summary=checked[0][2][:800]
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        existing=db.execute('SELECT * FROM documents WHERE id=?',(identifier,)).fetchone()
        if existing:
            if not can_read(user,existing) or existing['level']!=level:raise Problem('无法更新该资料或改变其开放级别',403)
            if existing['status']!='有效':raise Problem('资料存在质量或更正标记，不能通过导入解除，请先完成人工核验',409)
            if existing['version']==version:raise Problem('此版本已存在，请使用新版本号，原文不覆盖',409)
            if data.get('expected_version')!=existing['version']:raise Problem('资料版本已变化，请刷新后导入',409)
            db.execute('UPDATE documents SET title=?,category=?,year=?,version=?,pages=?,summary=?,paper_start=?,status=?,updated=? WHERE id=?',
                       (title,category,year,version,max(seen),summary,next(iter(offsets)),'有效',now(),identifier))
        else:
            db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(identifier,title,category,year,level,version,max(seen),'有效','',summary,next(iter(offsets)),now()))
        for scan,paper,content in checked:
            chunk_id=hashlib.sha256(f'{identifier}|{version}|{scan}'.encode()).hexdigest()
            db.execute('INSERT INTO document_chunks VALUES(?,?,?,?,?,?,?)',(chunk_id,identifier,version,scan,paper,content,'imported'))
        audit(db,user,'导入知识资料',detail={'document_id':identifier,'version':version,'pages':len(checked),'source':'人工提供文本'})
    return {'id':identifier,'version':version,'pages_imported':len(checked),'index':'首次检索时按授权范围构建 FAISS'}

def valid_sources(user,sources,allow_restricted):
    # Re-evaluate version, visibility and evidence content immediately before final answer.
    allowed={c['id']:c for c in available_chunks(user,allow_restricted)}
    for source in sources:
        if source.get('kind')=='policy':
            from ..agents.orchestrator import POLICY
            if source['content']!=POLICY:raise Problem('流程规则已变化，请重新提问',409)
            continue
        if source.get('kind')=='business':
            with connect() as db:
                row=db.execute('SELECT revision FROM businesses WHERE id=?',(source['business_id'],)).fetchone()
            if not row or row['revision']!=source['revision']:raise Problem('业务状态已变化，请重新提问',409)
            continue
        current=allowed.get(source['chunk_id'])
        if not current or current['version']!=source['version'] or current['content']!=source['content']:
            raise Problem('依据或授权已变化，请重新提问',409)
