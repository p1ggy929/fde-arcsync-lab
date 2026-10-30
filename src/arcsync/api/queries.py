from ..core.store import audit
from ..core.service import can_read, readable_document, Problem

def search_documents(db,user,qs):
    q=qs.get('q',[''])[0].strip()[:200]
    category=qs.get('category',[''])[0]
    year=qs.get('year',[''])[0]
    status=qs.get('status',[''])[0]
    terms=q.casefold().split()
    docs=[]
    for r in db.execute('SELECT * FROM documents ORDER BY year DESC'):
        if not can_read(user,r): continue
        d=dict(r)
        if category and d['category']!=category: continue
        if year and str(d['year'])!=year: continue
        if status and d['status']!=status: continue
        hay=' '.join(str(d[k]) for k in ('title','id','summary','category','year')).casefold()
        if terms and not all(t in hay for t in terms): continue
        d['match_reason']='目录与卷内摘要关键词匹配' if terms else '授权目录浏览'
        d['recommended']=d['status']=='有效'
        docs.append(d)
    audit(db,user,'检索授权档案',detail={'query':q,'category':category,'count':len(docs)})
    return ({'documents':docs,'source':'合成目录与固定摘要；未接入 AI 或真实 OCR'})

def document_page(db,user,identifier,qs):
    d=readable_document(db,user,identifier)
    page=int(qs.get('page',['6'])[0])
    if not 1<=page<=d['pages']: raise Problem('页码超出范围')
    imported=db.execute('SELECT * FROM document_chunks WHERE document_id=? AND version=? AND scan_page=?',(identifier,d['version'],page)).fetchone()
    has_import=db.execute('SELECT 1 FROM document_chunks WHERE document_id=? AND version=?',(identifier,d['version'])).fetchone()
    if has_import and not imported:raise Problem('此扫描页文本尚未导入',404)
    if imported:
        audit(db,user,'查看导入页文本',detail={'document_id':identifier,'version':d['version'],'scan_page':page})
        return {'document':d,'scan_page':page,'paper_page':imported['paper_page'],
                'paragraphs':imported['content'].split('\n'),'synthetic':False,'source':'人工提供文本；未核验原始扫描件'}
    from ..production import enabled
    if enabled():raise Problem('该页正文尚未导入，不能生成卷页内容',404)
    if d['status']=='待修复':
        content=['该页日期与页码存在识别歧义。','原记录保持不变，须由人工修复后重新核验。','此资料已从自动推荐样本中排除。']
    else:
        content=[f'一、关于{d["title"].replace("云州县","")}的说明',
          '根据相关资料整理，本页记录项目范围、实施安排及有关意见。各项内容应结合本卷上下文与后续更正文件进行核对。',
          d['summary'],
          '二、查阅与引用要求',
          '引用时应注明档号、资料版本和纸质页码。涉及后续调整的事项，以人工核实后的适用资料为依据。']
    audit(db,user,'查看合成卷页',detail={'document_id':d['id'],'version':d['version'],'scan_page':page})
    return ({'document':d,'scan_page':page,'paper_page':None if d['status']=='待修复' else d['paper_start']+page-1,
        'paragraphs':content,'synthetic':True})
