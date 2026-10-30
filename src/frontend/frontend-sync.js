'use strict';
// The same page generation is used across accounts. Stored metadata contains only a UI version.
(()=>{
 const loaded=document.querySelector('meta[name="arcsync-ui-version"]')?.content;
 let checking=null,pending=null,reloading=false;
 function hasUnsavedWork(){
  if(document.querySelector('.archive-chat[data-generating="true"]'))return true;
  const question=document.querySelector('#question');
  if(question?.value.trim())return true;
  return [...document.querySelectorAll('[data-ui-dirty="true"]')].some(node=>node.id!=='question');
 }
 function notice(){
  if(document.querySelector('#ui-update-notice'))return;
  const node=document.createElement('div');node.id='ui-update-notice';node.setAttribute('role','status');
  const message=document.createElement('span');message.textContent='界面已更新，完成当前输入或操作后将自动加载统一版本。';
  const button=document.createElement('button');button.type='button';button.textContent='现在更新';
  button.addEventListener('click',()=>{if(hasUnsavedWork()&&!confirm('当前输入或生成可能尚未完成，确定重新加载界面？'))return;reload(true);});
  node.append(message,button);document.body.append(node);
 }
 function reload(force=false){
  if(reloading)return;
  if(!force&&hasUnsavedWork()){notice();return;}
  try{
   const previous=JSON.parse(sessionStorage.getItem('arcsync-ui-refresh')||'null');
   if(!force&&previous?.version===pending&&Date.now()-previous.time<15000){notice();return;}
   sessionStorage.setItem('arcsync-ui-refresh',JSON.stringify({version:pending,time:Date.now()}));
  }catch{/* Private browsing may disable storage; version checks still work. */}
  reloading=true;location.reload();
 }
 function ensure(version){
  if(!loaded||version!==loaded){pending=version;reload();return false;}
  return true;
 }
 async function check(){
  if(checking)return checking;
  checking=(async()=>{
   try{
    const response=await fetch('/api/frontend/version',{cache:'no-store'});
    if(!response.ok)return true;
    const data=await response.json();return ensure(data.version);
   }catch{return true;}
   finally{checking=null;}
  })();
  return checking;
 }
 function saved(form){form?.querySelectorAll('[data-ui-dirty]').forEach(node=>delete node.dataset.uiDirty);if(pending)check();}
 window.ArcSyncUI=Object.freeze({version:loaded,ensure,check,saved});
 document.addEventListener('input',event=>{if(event.target.matches('input,textarea,select'))event.target.dataset.uiDirty='true';});
 window.addEventListener('focus',check);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)check();});
 window.addEventListener('error',event=>{if(event.target instanceof HTMLScriptElement||event.target instanceof HTMLLinkElement)check();},true);
 setInterval(()=>{if(!document.hidden)check();},15000);
 document.addEventListener('DOMContentLoaded',check,{once:true});
})();
