"""Tool allowlist; the model never executes SQL, shell, approvals or disclosure writes."""
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from ..core.store import connect
from ..core.service import Problem
from . import access
from .policy import POLICY
from ..data_models.vectors import hybrid_search
from ..data_models.knowledge import available_chunks
from starlette.concurrency import run_in_threadpool

class Arguments(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,str_strip_whitespace=True)
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

class Validity(Arguments):
    document_id:str=Field(min_length=1,max_length=100)
    version:str=Field(min_length=1,max_length=40)
class Selection(Validity):
    scan_page:int=Field(ge=1,le=5000)
class CaseUpdate(Arguments):
    case_id:str|None=Field(default=None,max_length=120)
    revision:int=Field(default=0,ge=0)
    title:str=Field(default='',max_length=100)
    applicant:str=Field(default='',max_length=100)
    purpose:str=Field(default='',max_length=500)
    usage:str=Field(default='',max_length=100)
    archives:list[Selection]=Field(default_factory=list,max_length=20)
class CaseGet(Arguments):
    case_id:str|None=Field(default=None,max_length=120)
class CaseVersion(Arguments):
    case_id:str=Field(min_length=1,max_length=120)
    revision:int=Field(ge=1)
class Draft(CaseVersion):
    validation_id:str=Field(min_length=1,max_length=120)

MODELS={'search_archives':Search,'read_archive_page':Page,'get_business_status':Business,
        'check_archive_validity':Validity,'update_access_case':CaseUpdate,'get_access_case':CaseGet,
        'validate_access_request':CaseVersion,'create_access_draft':Draft,'get_access_rules':Arguments}
DESCRIPTIONS={
 'check_archive_validity':'核对档案版本、开放状态、质量和更正关系，不代表人工确认。',
 'update_access_case':'完整替换本人查阅需求。新建时case_id=null、revision=0；修改须提供当前版本。空字段表示待补充。不是正式申请。',
 'get_access_case':'读取本人事项及当前版本，case_id=null时列出最近事项，供多轮继续。',
 'validate_access_request':'须先读取选定页和更正正文并核对有效性；服务端校验当前事项版本。passed只表示可以创建草稿。',
 'create_access_draft':'只在用户明确允许本次创建草稿且当前版本校验passed后调用。必须使用有效校验凭证，不提交审批。',
 'get_access_rules':'读取平台查阅协同流程和人工审核边界，回答流程问题时使用。',
 'search_archives':'在当前账号授权且有效的资料中进行语义与关键词混合检索，返回来源编号。不得改变开放范围。',
 'read_archive_page':'读取已导入的指定档案扫描页及纸质页码；未导入页文本时只返回目录摘要并说明限制。',
 'get_business_status':'按业务编号查询当前流程状态、责任岗位及修订号，只读，不修改业务。'}
def strict_schema(model):
    schema=model.model_json_schema()
    def visit(node):
        if isinstance(node,dict):
            node.pop('default',None)
            if node.get('type')=='object':
                node['additionalProperties']=False
                node['required']=list(node.get('properties',{}))
            for value in node.values():visit(value)
        elif isinstance(node,list):
            for item in node:visit(item)
    visit(schema)
    return schema

TOOL_SCHEMAS=[{'type':'function','function':{'name':name,'description':DESCRIPTIONS[name],'strict':True,'parameters':strict_schema(model)}} for name,model in MODELS.items()]


class ToolBox:
    def __init__(self,user,embedding,allow_restricted,create_authorized=False):
        self.user=user;self.embedding=embedding;self.allow_restricted=allow_restricted
        self.sources=[]
        self.reads={};self.checks={};self.create_authorized=create_authorized

    def remember(self,chunk):
        key=chunk['id']
        existing=next((s for s in self.sources if s.get('chunk_id')==key),None)
        if existing:return existing
        if len(self.sources)>=12:raise Problem('本次依据数量达到上限，请缩小范围',409)
        source={'label':'C'+str(len(self.sources)+1),'kind':'archive','chunk_id':key,'document_id':chunk['document_id'],
                'version':chunk['version'],'title':chunk['title'],'filename':chunk.get('filename',chunk['title']),'scan_page':chunk['scan_page'],'paper_page':chunk['paper_page'],
                'source':chunk['source'],'content':chunk['content'],'correction':chunk.get('correction','')}
        self.sources.append(source);return source

    async def schemas(self):
        allowed=self.create_authorized and self.user.get('user_id') and await run_in_threadpool(access.can_create,self.user)
        return [s for s in TOOL_SCHEMAS if allowed or s['function']['name']!='create_access_draft']

    def evidence(self,kind,title,content,**fields):
        if len(self.sources)>=12:raise Problem('本次依据数量达到上限，请缩小范围',409)
        source={'label':'C'+str(len(self.sources)+1),'kind':kind,'title':title,'content':content,**fields}
        self.sources.append(source)
        return source

    async def execute(self,name,arguments):
        if name not in MODELS:raise Problem('智能体请求了未授权工具',403)
        try:args=MODELS[name].model_validate(arguments)
        except ValidationError:raise Problem('智能体工具参数无效') from None
        if name=='get_access_rules':
            return {'source':self.evidence('policy','平台查阅协同规则',POLICY)}
        if name=='get_access_case':return await run_in_threadpool(access.view,self.user,args.case_id)
        if name=='check_archive_validity':
            snapshot,metadata=await run_in_threadpool(access.inspect,self.user,args.document_id,args.version)
            if snapshot['document']['level']!='公开' and not self.allow_restricted:raise Problem('受限资料不可进入当前模型上下文',403)
            self.checks[args.document_id]=access.fingerprint(snapshot)
            source=self.evidence('validity',metadata['title'],str(metadata),document_id=args.document_id,version=args.version,fingerprint=access.fingerprint(snapshot))
            return {'metadata':metadata,'source':source,'notice':'须读取当前页及后续更正正文；本结果不是人工核验'}
        if name=='update_access_case':
            data=args.model_dump();identifier=data.pop('case_id');revision=data.pop('revision')
            return await run_in_threadpool(access.update,self.user,identifier,revision,data)
        if name=='validate_access_request':
            return await run_in_threadpool(access.validate,self.user,args.case_id,args.revision,self.reads,self.checks)
        if name=='create_access_draft':
            if len(self.sources)>=12:raise Problem('本次依据数量达到上限，请在新一轮创建草稿',409)
            result=await run_in_threadpool(access.create,self.user,args.case_id,args.revision,args.validation_id,self.create_authorized)
            result['source']=self.evidence('access_case','查阅申请草稿',str(result),case_id=args.case_id,revision=args.revision)
            return result
        if name=='search_archives':
            chunks=await hybrid_search(self.user,args.query,self.embedding,self.allow_restricted,args.limit,args.category,args.year)
            return {'sources':[self.remember(c) for c in chunks],'notice':'目录摘要与人工提供文本不等同于经过核验的原卷证据'}
        if name=='read_archive_page':
            chunks=await run_in_threadpool(available_chunks,self.user,self.allow_restricted)
            allowed=[c for c in chunks if c['document_id']==args.document_id]
            if not allowed:raise Problem('档案不存在或不在当前模型授权范围内',404)
            found=next((c for c in allowed if c['scan_page']==args.scan_page),None)
            if found:
                snapshot,_=await run_in_threadpool(access.inspect,self.user,args.document_id,found['version'])
                self.reads[(args.document_id,args.scan_page)]=access.fingerprint(snapshot)
                return {'source':self.remember(found)}
            if allowed[0]['source']=='catalog':
                return {'source':self.remember(allowed[0]),'notice':'尚未导入该页文本，仅有目录摘要，不能确认卷内页码'}
            raise Problem('该扫描页尚未导入，不能生成页内容',404)
        def status():
            with connect() as db:
                row=db.execute('SELECT * FROM businesses WHERE id=?',(args.business_id,)).fetchone()
                if not row or not access.business_visible(db,self.user,row):raise Problem('业务不存在或不在授权范围内',404)
                return {key:row[key] for key in ('id','title','status','owner','revision')}
        result=await run_in_threadpool(status)
        if len(self.sources)>=12:raise Problem('本次依据数量达到上限，请缩小范围',409)
        label='C'+str(len(self.sources)+1)
        source={'label':label,'kind':'business','title':result['title'],'business_id':result['id'],
                'revision':result['revision'],'content':str(result)}
        self.sources.append(source)
        return {'source':source}
