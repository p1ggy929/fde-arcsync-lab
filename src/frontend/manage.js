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
function bind(form,run){form?.addEventListener('submit',async event=>{event.preventDefault();const button=form.querySelector('button');button.disabled=true;try{await run();notice.textContent='已保存';}catch(error){notice.textContent=error.message;}finally{button.disabled=false;}});}
bind(document.querySelector('#upload'),async()=>{const file=document.querySelector('#file').files[0];if(!file)throw Error('请选择文件');if(file.size>10*1024*1024)throw Error('文件最多10MB');await action('/api/admin/knowledge?filename='+encodeURIComponent(file.name),'POST',file,true);location.reload();});
bind(document.querySelector('#prompt'),async()=>{const form=document.querySelector('#prompt');const result=await action('/api/admin/prompt','PUT',{content:document.querySelector('#content').value,revision:Number(form.dataset.revision)});form.dataset.revision=result.revision;});
for(const button of document.querySelectorAll('[data-delete]'))button.addEventListener('click',async()=>{if(!confirm('删除该制度？相关历史引用将失效。'))return;button.disabled=true;try{await action('/api/admin/knowledge/'+encodeURIComponent(button.dataset.delete),'DELETE',{});location.reload();}catch(error){notice.textContent=error.message;button.disabled=false;}});
for(const form of document.querySelectorAll('.flag'))bind(form,()=>action('/api/admin/conversations/'+encodeURIComponent(form.dataset.id)+'/flag','PUT',{flagged:form.elements.flagged.checked,reason:form.elements.reason.value}));

for(const form of document.querySelectorAll('.submit-draft'))bind(form,async()=>{const result=await action('/api/access-drafts/'+encodeURIComponent(form.dataset.id)+'/submit','POST',{});location.href=form.dataset.home+'#detail/'+encodeURIComponent(result.id);});
