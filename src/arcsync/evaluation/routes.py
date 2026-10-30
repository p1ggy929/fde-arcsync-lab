"""评测任务接口：管理员读；主任/保护/工程岗位执行，独立管理员复核。"""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from ..api.security import admin_user
from ..api.routes import json_body
from ..core.service import Problem, require
from ..data_models.config import ModelError
from . import repository, profiles
from .specs import suite

router=APIRouter(prefix='/api/admin/evaluation')


def ok(data,message='操作成功'):return {'code':0,'message':message,'data':data}


@router.get('/options')
def options(user=Depends(admin_user)):
    models=[]
    for key,(label,config) in profiles.profiles().items():
        try:config.validate();configured=True
        except ModelError:configured=False
        models.append({'id':key,'label':label,'configured':configured,'model':profiles.metadata(config)})
    return ok({'models':models,'suite':suite(),'notice':'隔离合成数据执行；真实模型会产生费用。不会自动确认草稿。'})


@router.get('/jobs')
def jobs(user=Depends(admin_user)):return ok(repository.listed())


@router.post('/jobs',status_code=202)
def create(data:dict=Depends(json_body),user=Depends(admin_user)):
    require(user,('director','protector','engineer'))
    if set(data)-{'profile','case_ids'}:raise Problem('评测参数包含未支持字段')
    if not isinstance(data.get('profile','current'),str):raise Problem('模型档案标识无效')
    try:job=repository.create(user,data.get('profile','current'),data.get('case_ids',list(range(1,31))))
    except ModelError as error:raise Problem(str(error),503) from None
    return ok(job,'评测已排队；执行时将产生模型调用')


@router.get('/compare')
def compare(left:str,right:str,user=Depends(admin_user)):return ok(repository.compare(left,right))


@router.get('/jobs/{identifier}')
def detail(identifier:str,user=Depends(admin_user)):return ok(repository.detail(identifier))


@router.get('/jobs/{identifier}/report')
def report(identifier:str,user=Depends(admin_user)):
    job=repository.detail(identifier)
    with repository.db() as db:job['review_audit']=[dict(r) for r in db.execute('SELECT * FROM evaluation_reviews WHERE job_id=? ORDER BY created',(identifier,))]
    return JSONResponse(ok(job),headers={'Content-Disposition':f'attachment; filename="evaluation-{job["id"]}.json"'})


@router.post('/jobs/{identifier}/stop')
def stop(identifier:str,data:dict=Depends(json_body),user=Depends(admin_user)):
    require(user,('director','protector','engineer'))
    return ok(repository.stop(user,identifier),'已请求停止；已发生的模型费用不退还')


@router.put('/jobs/{identifier}/results/{case_id}/review')
def review(identifier:str,case_id:int,data:dict=Depends(json_body),user=Depends(admin_user)):
    return ok(repository.review(user,identifier,case_id,data),'人工复核已保存')
