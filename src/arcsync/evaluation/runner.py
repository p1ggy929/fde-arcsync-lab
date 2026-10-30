"""后台持久队列；Web重启不自动重跑付费请求，子进程随停止请求终止。"""
import asyncio
from dataclasses import asdict
import json
import os
import sys
import tempfile
import uuid
from starlette.concurrency import run_in_threadpool
from ..core.store import ROOT
from . import repository
from .profiles import select, metadata


async def terminate(process):
    if process and process.returncode is None:
        try:process.terminate()
        except ProcessLookupError:return
        try:await asyncio.wait_for(process.wait(),5)
        except asyncio.TimeoutError:process.kill();await process.wait()


async def execute(identifier,worker):
    process=None;sandbox=None
    try:
        job=await run_in_threadpool(repository.detail,identifier)
        if not await run_in_threadpool(repository.owner_enabled,identifier):raise ValueError('发起账号已停用')
        config=select(job['profile'])
        if metadata(config)!=job['snapshot']['model']:raise ValueError('模型档案已变化')
        payload={'snapshot':job['snapshot'],'owner':job['owner'],'config':asdict(config)}
        sandbox=tempfile.TemporaryDirectory(prefix='arcsync-evaluation-job-')
        environment=os.environ.copy();environment['PYTHONPATH']=str(ROOT/'src')
        environment['ARCSYNC_EVALUATION_TEMP_ROOT']=sandbox.name
        # 运行器的stdout是仅含结果的私有JSON行协议，密钥只经stdin传递。
        process=await asyncio.create_subprocess_exec(sys.executable,'-m','arcsync.evaluation.worker',cwd=ROOT,env=environment,
                    stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=None,limit=4*1024*1024)
        process.stdin.write(json.dumps(payload,ensure_ascii=False).encode());await process.stdin.drain();process.stdin.close()
        count=0
        while True:
            read=asyncio.create_task(process.stdout.readline())
            try:
                while not read.done():
                    await asyncio.wait({read},timeout=2)
                    if await run_in_threadpool(repository.pulse,identifier,worker):
                        await terminate(process)
                        await run_in_threadpool(repository.finish,identifier,worker,'cancelled')
                        return
                line=read.result()
            finally:
                if not read.done():read.cancel();await asyncio.gather(read,return_exceptions=True)
            if not line:break
            result=json.loads(line)
            if result.get('case_id') not in job['snapshot']['case_ids']:raise ValueError('用例编号无效')
            await run_in_threadpool(repository.save_result,identifier,worker,result);count+=1
        code=await process.wait()
        good=code==0 and count==len(job['snapshot']['case_ids'])
        await run_in_threadpool(repository.finish,identifier,worker,'completed' if good else 'failed','' if good else '评测子进程未完整结束；已完成结果保留')
    except asyncio.CancelledError:
        await terminate(process)
        await run_in_threadpool(repository.finish,identifier,worker,'interrupted','服务重启或执行器停止，未自动重跑')
        raise
    except Exception:
        await run_in_threadpool(repository.finish,identifier,worker,'failed','评测无法执行，请核对配置、版本及模型服务；任务不会自动重试')
    finally:
        await terminate(process)
        if sandbox:sandbox.cleanup()


async def serve():
    worker=uuid.uuid4().hex
    while True:
        try:
            identifier=await run_in_threadpool(repository.claim,worker)
            if identifier:await execute(identifier,worker)
            else:await asyncio.sleep(2)
        except asyncio.CancelledError:raise
        except Exception:
            import logging
            logging.getLogger('arcsync.evaluation').error('评测队列暂不可用，将重试读取队列')
            await asyncio.sleep(5)
