'use strict';
const notice=document.querySelector('#notice');
async function action(url,method,data,raw=false){
  const headers={'X-ArcSync-Request':'1'};
  if(!raw)headers['Content-Type']='application/json';
  notice.textContent='正在处理，请稍候…';
  const response=await fetch(url,{method,headers,body:raw?data:JSON.stringify(data)});
  const result=await response.json();
  if(!response.ok)throw Error(result.message||'操作失败');
  return result;
}
function bind(form,run){form?.addEventListener('submit',async event=>{event.preventDefault();const button=form.querySelector('button');button.disabled=true;try{await run();window.ArcSyncUI?.saved(form);notice.textContent='已保存';}catch(error){notice.textContent=error.message;}finally{button.disabled=false;}});}
bind(document.querySelector('#upload'),async()=>{const file=document.querySelector('#file').files[0];if(!file)throw Error('请选择文件');if(file.size>10*1024*1024)throw Error('文件最多10MB');await action('/api/admin/knowledge?filename='+encodeURIComponent(file.name),'POST',file,true);location.reload();});
bind(document.querySelector('#prompt'),async()=>{const form=document.querySelector('#prompt');const result=await action('/api/admin/prompt','PUT',{content:document.querySelector('#content').value,revision:Number(form.dataset.revision)});form.dataset.revision=result.revision;});
for(const button of document.querySelectorAll('[data-delete]'))button.addEventListener('click',async()=>{if(!confirm('删除该制度？相关历史引用将失效。'))return;button.disabled=true;try{await action('/api/admin/knowledge/'+encodeURIComponent(button.dataset.delete),'DELETE',{});location.reload();}catch(error){notice.textContent=error.message;button.disabled=false;}});
for(const form of document.querySelectorAll('.flag'))bind(form,()=>action('/api/admin/conversations/'+encodeURIComponent(form.dataset.id)+'/flag','PUT',{flagged:form.elements.flagged.checked,reason:form.elements.reason.value}));

for(const form of document.querySelectorAll('.submit-draft'))bind(form,async()=>{const result=await action('/api/access-drafts/'+encodeURIComponent(form.dataset.id)+'/submit','POST',{});location.href=form.dataset.home+'#detail/'+encodeURIComponent(result.id);});

// 评测模型输出只用textContent展示，不解析模型HTML；轮询只读取结果，不重复发起付费任务。
if(document.querySelector('#evaluation-start')){
  const el=id=>document.getElementById(id);
  const write=(parent,tag,text)=>{const node=document.createElement(tag);node.textContent=text;parent.append(node);return node;};
  let selectedJob=null,canRun=false;
  async function read(url){const response=await fetch(url);const value=await response.json();if(!response.ok)throw Error(value.message||'读取失败');return value.data;}
  const label={queued:'排队中',running:'运行中',completed:'已执行，待复核',failed:'执行失败',cancelled:'已停止',interrupted:'执行中断',passed:'通过',needs_review:'待人工复核',blocked:'发布阻断',ready_for_release_review:'可进入发布审批'};
  function text(value){return JSON.stringify(value,null,2);}
  async function loadJobs(){
    const jobs=await read('/api/admin/evaluation/jobs');const root=el('evaluation-jobs');root.replaceChildren();
    for(const id of ['evaluation-left','evaluation-right']){const select=el(id),previous=select.value;select.replaceChildren();for(const job of jobs){const option=document.createElement('option');option.value=job.id;option.textContent=job.created+' · '+job.id.slice(0,8);select.append(option);}if(jobs.some(j=>j.id===previous))select.value=previous;}
    for(const job of jobs){const row=write(root,'p',job.created+' · '+(label[job.status]||job.status)+' · '+job.profile+' ');const button=write(row,'button','查看 '+job.id.slice(0,8));button.addEventListener('click',()=>show(job.id).catch(fail));}
  }
  function fail(error){notice.textContent=error.message||'评测服务暂不可用';}
  async function show(identifier){
    selectedJob=identifier;const job=await read('/api/admin/evaluation/jobs/'+identifier);if(selectedJob!==identifier)return;
    const root=el('evaluation-detail');root.replaceChildren();const header=write(root,'section','');header.className='card';
    write(header,'h2','评测 '+identifier.slice(0,8)+' · '+(label[job.status]||job.status));
    write(header,'p','发布门禁：'+label[job.gate.status]+' · 缺失 '+job.gate.missing_cases.length+' 项 · 待复核 '+job.gate.pending_cases.length+' 项');
    write(header,'pre',text({summary:job.summary,gate:job.gate,code:job.snapshot.code_version,prompt_revision:job.snapshot.prompt.revision,model:job.snapshot.model}));
    if(job.error)write(header,'p',job.error);
    const report=write(header,'a','下载完整报告');report.href='/api/admin/evaluation/jobs/'+identifier+'/report';
    if(['queued','running'].includes(job.status)&&canRun){const stop=write(header,'button','停止评测');stop.addEventListener('click',async()=>{try{await action('/api/admin/evaluation/jobs/'+identifier+'/stop','POST',{});await show(identifier);await loadJobs();}catch(error){fail(error);}});}
    const cases=new Map(job.snapshot.suite.cases.map(item=>[item.id,item]));
    for(const row of job.results){const card=write(root,'article','');card.className='card';const spec=cases.get(row.case_id);
      write(card,'h3',row.case_id+' · '+spec.category+' · '+(row.result.score.auto_status==='failed'?'自动检查失败':label[row.review]));
      write(card,'p',spec.question);write(card,'p','期望：'+spec.expected_behavior);
      write(card,'pre',text({checks:row.result.score.checks,rubric:row.result.score.human_rubric,duration_ms:row.result.duration_ms,usage:row.result.usage,reserved_micro_usd:row.result.reserved_micro_usd}));
      for(const turn of row.result.turns||[]){write(card,'h4','提问：'+turn.question);write(card,'pre',turn.answer||turn.error||'无完整回答');const detail=write(card,'details','');write(detail,'summary','工具、来源及实际执行问题');write(detail,'pre',text(turn));}
      const trace=write(card,'details','');write(trace,'summary','逐轮参数、耗时及结果摘要');write(trace,'pre',text(row.result.trace||[]));
      write(card,'p','复核：'+(row.reason||'尚未复核')+' · '+(row.reviewer||'无复核人'));
      if(!['queued','running'].includes(job.status)){
        const form=write(card,'form','');const decision=document.createElement('select');for(const value of ['needs_review','passed','failed']){const option=document.createElement('option');option.value=value;option.textContent=label[value];decision.append(option);}decision.value=row.review;form.append(decision);
        const reason=document.createElement('textarea');reason.placeholder='结合预期行为与引用填写独立复核理由';reason.required=true;reason.maxLength=2000;reason.value=row.reason;form.append(reason);const button=write(form,'button','保存复核');button.type='submit';
        form.addEventListener('submit',async event=>{event.preventDefault();button.disabled=true;try{await action('/api/admin/evaluation/jobs/'+identifier+'/results/'+row.case_id+'/review','PUT',{decision:decision.value,reason:reason.value,revision:row.revision});await show(identifier);}catch(error){fail(error);button.disabled=false;}});
      }
    }
  }
  bind(el('evaluation-start'),async()=>{const ranges={all:[1,30],policy:[1,10],tools:[11,20],safety:[21,25],context:[26,30]};const [first,last]=ranges[el('evaluation-scope').value];const case_ids=Array.from({length:last-first+1},(_,i)=>first+i);const response=await action('/api/admin/evaluation/jobs','POST',{profile:el('evaluation-profile').value,case_ids});await loadJobs();await show(response.data.id);});
  bind(el('evaluation-compare'),async()=>{const left=el('evaluation-left').value,right=el('evaluation-right').value;if(!left||!right)throw Error('请先选择两次评测');el('evaluation-comparison').textContent=text(await read('/api/admin/evaluation/compare?left='+encodeURIComponent(left)+'&right='+encodeURIComponent(right)));});
  el('evaluation-refresh').addEventListener('click',()=>loadJobs().catch(fail));
  async function initialize(){
    const session=await fetch('/api/session').then(r=>r.json());canRun=['director','protector','engineer'].includes(session.role);
    const options=await read('/api/admin/evaluation/options');const select=el('evaluation-profile');
    for(const model of options.models){const option=document.createElement('option');option.value=model.id;option.textContent=model.label+' · '+model.model.llm.model+(model.configured?'':'（未配置）');option.disabled=!model.configured;select.append(option);}
    const configured=options.models.some(m=>m.configured);el('evaluation-start').querySelector('button').disabled=!configured||!canRun;
    if(configured)select.value=options.models.find(m=>m.configured).id;
    el('evaluation-config').textContent=!configured?'请先由运维配置模型后再运行，题库仍可查看。':!canRun?'当前岗位可查看及独立复核，运行任务请由主任、保护或工程岗位发起。':'执行会调用真实模型，请先核对额度。';
    for(const item of options.suite.cases){const detail=write(el('evaluation-cases'),'details','');write(detail,'summary',item.id+' · '+item.category+' · '+item.question);write(detail,'pre',text(item));}
    await loadJobs();
  }
  initialize().catch(fail);
  const timer=setInterval(async()=>{if(document.hidden)return;try{await loadJobs();if(selectedJob&&!el('evaluation-detail').contains(document.activeElement))await show(selectedJob);}catch(error){fail(error);}},5000);
  window.addEventListener('pagehide',()=>clearInterval(timer),{once:true});
}
