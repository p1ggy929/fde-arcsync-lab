"""Tool allowlist; the model never executes SQL, shell, approvals or disclosure writes."""
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from ..core.store import connect
from ..core.service import Problem, readable_document
from ..data_models.vectors import hybrid_search
from ..data_models.knowledge import available_chunks
from starlette.concurrency import run_in_threadpool

class Arguments(BaseModel):
    model_config=ConfigDict(extra='forbid')
class Search(Arguments):
    query:str=Field(min_length=1,max_length=300)
    limit:int=Field(default=5,ge=1,le=8)
    category:str=Field(default='',max_length=60)
    year:int|None=Field(default=None,ge=1000,le=2100)
class Page(Arguments):
    document_id:str=Field(min_length=1,max_length=100)
    scan_page:int=Field(ge=1,le=5000)
class Business(Arguments):
    business_id:str=Field(min_length=1,max_length=120)

MODELS={'search_archives':Search,'read_archive_page':Page,'get_business_status':Business}
DESCRIPTIONS={
 'search_archives':'在当前账号授权且有效的资料中进行语义与关键词混合检索，返回来源编号。不得改变开放范围。',
 'read_archive_page':'读取已导入的指定档案扫描页及纸质页码；未导入页文本时只返回目录摘要并说明限制。',
 'get_business_status':'按业务编号查询当前流程状态、责任岗位及修订号，只读，不修改业务。'}
TOOL_SCHEMAS=[{'type':'function','function':{'name':name,'description':DESCRIPTIONS[name],'parameters':model.model_json_schema()}} for name,model in MODELS.items()]

class ToolBox:
    def __init__(self,user,embedding,allow_restricted):
        self.user=user;self.embedding=embedding;self.allow_restricted=allow_restricted
        self.sources=[]

    def remember(self,chunk):
        key=chunk['id']
        existing=next((s for s in self.sources if s.get('chunk_id')==key),None)
        if existing:return existing
        if len(self.sources)>=12:raise Problem('本次依据数量达到上限，请缩小范围',409)
        source={'label':'C'+str(len(self.sources)+1),'kind':'archive','chunk_id':key,'document_id':chunk['document_id'],
                'version':chunk['version'],'title':chunk['title'],'scan_page':chunk['scan_page'],'paper_page':chunk['paper_page'],
                'source':chunk['source'],'content':chunk['content'],'correction':chunk.get('correction','')}
        self.sources.append(source);return source

    async def execute(self,name,arguments):
        if name not in MODELS:raise Problem('智能体请求了未授权工具',403)
        try:args=MODELS[name].model_validate(arguments)
        except ValidationError:raise Problem('智能体工具参数无效') from None
        if name=='search_archives':
            chunks=await hybrid_search(self.user,args.query,self.embedding,self.allow_restricted,args.limit,args.category,args.year)
            return {'sources':[self.remember(c) for c in chunks],'notice':'目录摘要与人工提供文本不等同于经过核验的原卷证据'}
        if name=='read_archive_page':
            chunks=await run_in_threadpool(available_chunks,self.user,self.allow_restricted)
            allowed=[c for c in chunks if c['document_id']==args.document_id]
            if not allowed:raise Problem('档案不存在或不在当前模型授权范围内',404)
            found=next((c for c in allowed if c['scan_page']==args.scan_page),None)
            if found:return {'source':self.remember(found)}
            if allowed[0]['source']=='catalog':
                return {'source':self.remember(allowed[0]),'notice':'尚未导入该页文本，仅有目录摘要，不能确认卷内页码'}
            raise Problem('该扫描页尚未导入，不能生成页内容',404)
        def status():
            with connect() as db:
                row=db.execute('SELECT id,title,status,owner,revision FROM businesses WHERE id=?',(args.business_id,)).fetchone()
                if not row:raise Problem('业务不存在',404)
                return dict(row)
        result=await run_in_threadpool(status)
        if len(self.sources)>=12:raise Problem('本次依据数量达到上限，请缩小范围',409)
        label='C'+str(len(self.sources)+1)
        source={'label':label,'kind':'business','title':result['title'],'business_id':result['id'],
                'revision':result['revision'],'content':str(result)}
        self.sources.append(source)
        return {'source':source}
