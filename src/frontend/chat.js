'use strict';
// 页面只保存当前视图，问题和回答不写入localStorage；服务端是历史记录的唯一来源。
const $=selector=>document.querySelector(selector);
let user=null,sessionId=null,controller=null,busy=false,sessions=[],nextOffset=0,beforeTurn=null,listSequence=0;
const question=$('#question'),messages=$('#messages'),status=$('#generation-status');

function element(tag,className,text){
 const node=document.createElement(tag);if(className)node.className=className;
 if(text!==undefined)node.textContent=text;return node;
}
// 网络异常和协议解析异常也转换为中文，避免直接显示浏览器内部英文错误。
function errorText(error){return /[\u4e00-\u9fff]/.test(error.message||'')?error.message:'连接中断或服务响应异常，请稍后重试';}
function setStatus(text,loading=false){status.textContent=text;status.classList.toggle('busy',loading);}
function scrollBottom(){const view=$('#message-scroll');view.scrollTop=view.scrollHeight;}
function setBusy(value){
 busy=value;$('#allow-draft').disabled=value;$('#send').disabled=value;$('#stop').disabled=!value;question.disabled=value;
 $('#new-chat').disabled=value;$('#refresh-messages').disabled=value;$('#refresh-sessions').disabled=value;
 document.querySelectorAll('.session-button,[data-question],#more-sessions,#older-messages').forEach(button=>button.disabled=value);
}
function authenticationLost(){
 controller?.abort();user=null;sessionId=null;sessions=[];messages.replaceChildren();renderSessions();
 $('#chat-welcome').hidden=false;$('#user-name').textContent='未登录';
 $('#configuration-notice').hidden=false;$('#configuration-notice').textContent='请先通过欢迎页登录，再返回智能查阅。';
 $('#back-portal').href='/';$('#back-portal').textContent='前往登录 ↗';setStatus('请先登录或重新登录');
}
async function requestJSON(url,options={}){
 const response=await fetch(url,options);let body;
 try{body=await response.json();}catch{throw new Error('服务返回格式异常，请稍后重试');}
 if(!response.ok){if(response.status===401)authenticationLost();throw new Error(body.message||body.error||'请求未完成');}
 return body;
}
function changeURL(){
 const url=new URL(location.href);if(sessionId)url.searchParams.set('session',sessionId);else url.searchParams.delete('session');
 history.replaceState(null,'',url.pathname+url.search);
}
function renderSessions(){
 const list=$('#session-list');list.replaceChildren();
 if(!sessions.length)list.append(element('div','empty-list','还没有会话，开始一个问题吧'));
 for(const item of sessions){
  const button=element('button','session-button'+(item.id===sessionId?' active':''));button.type='button';button.disabled=busy;
  button.append(element('strong','',item.title));button.append(element('small','',new Date(item.updated_at).toLocaleDateString('zh-CN')));
  button.addEventListener('click',()=>openSession(item.id).catch(error=>setStatus(errorText(error))));
  list.append(button);
 }
 $('#more-sessions').hidden=nextOffset===null;
}
async function loadSessions(append=false){
 const sequence=++listSequence,owner=user?.user_id;
 const response=await requestJSON('/chat/sessions?limit=20&offset='+(append?nextOffset||0:0));
 if(sequence!==listSequence||!user||user.user_id!==owner)return;
 sessions=append?[...sessions,...response.data.sessions]:response.data.sessions;nextOffset=response.data.next_offset;renderSessions();
}
function addMessage(role,text='',state='completed'){
 $('#chat-welcome').hidden=true;
 const article=element('article','message '+role+(state==='failed'?' error':''));article.dataset.role=role;
 const label=element('div','message-label');label.append(element('span','',role==='user'?'你':'问'));
 label.append(document.createTextNode(role==='user'?'我的问题':'档案助手'));
 const body=element('div','message-body',text),meta=element('div','message-meta');
 article.append(label,body,meta);messages.append(article);return {article,body,meta};
}
function showCitations(article,items){
 article.querySelector('.citations')?.remove();if(!items?.length)return;
 const container=element('section','citations');container.append(element('strong','','引用的档案来源'));
 for(const citation of items){
  const details=element('details','citation'),summary=element('summary');
  const location=[citation.version,citation.section,citation.page!==null&&citation.page!==undefined?'第'+citation.page+'页':null].filter(Boolean).join(' · ');
  summary.textContent='['+citation.id+'] '+citation.filename+(location?' · '+location:'');
  // 使用文本节点显示原文，档案正文即使含HTML或脚本也不会执行。
  details.append(summary,element('p','',citation.snippet));container.append(details);
 }
 article.append(container);
}
async function openSession(id,older=false){
 if(busy)return;
 const owner=user?.user_id;
 const suffix=older&&beforeTurn?'&before_turn='+beforeTurn:'';
 const response=await requestJSON('/chat/sessions/'+encodeURIComponent(id)+'/messages?limit=20'+suffix);
 if(!user||user.user_id!==owner)return;
 sessionId=id;beforeTurn=response.data.next_before_turn;
 if(!older)messages.replaceChildren();
 const existing=older?[...messages.children]:[];if(older)messages.replaceChildren();
 for(const item of response.data.messages){
  const failed=['failed','cancelled','unavailable'].includes(item.status);
  const row=addMessage(item.role,failed?item.error_message:item.content,failed?'failed':item.status);
  if(item.role==='assistant'){
   row.meta.textContent=item.status==='completed'?'已保存 · 内容请结合档案原文核对':item.status==='generating'?'该回答尚在生成，可稍后刷新消息':'未完成，不计入下轮上下文';
   if(item.status==='completed')showCitations(row.article,item.citations);
  }
 }
 if(older)messages.append(...existing);
 $('#older-messages').hidden=!response.data.has_more;$('#chat-welcome').hidden=!!messages.children.length;
 changeURL();renderSessions();setStatus('会话已恢复，可继续追问');if(!older)scrollBottom();
}
function newChat(){
 if(busy)return;sessionId=null;beforeTurn=null;messages.replaceChildren();$('#older-messages').hidden=true;
 $('#chat-welcome').hidden=false;question.value='';updateCount();changeURL();renderSessions();setStatus('描述你的问题，答案会附上档案来源');question.focus();
}
function updateCount(){
 $('#question-count').textContent=question.value.length+' / 2000';
 question.style.height='auto';question.style.height=Math.min(180,Math.max(54,question.scrollHeight))+'px';
}
function typewriter(node){
 // 将网络分块和视觉打字速度解耦，避免一次收到多个token时整段跳出。
 let target=[],shown=0,timer=null,resolve=null;
 const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
 function tick(){
  shown=Math.min(target.length,shown+(reduced?target.length:4));node.textContent=target.slice(0,shown).join('');scrollBottom();
  if(shown>=target.length){clearInterval(timer);timer=null;if(resolve){resolve();resolve=null;}}
 }
 function schedule(){if(!timer)timer=setInterval(tick,18);}
 return {
  append(text){target.push(...Array.from(text));schedule();},
  finish(text){
   if(target.join('')!==text){target=Array.from(text);shown=0;}
   if(shown>=target.length)return Promise.resolve();
   schedule();return new Promise(done=>resolve=done);
  },
  cancel(){clearInterval(timer);timer=null;if(resolve)resolve();resolve=null;target=[];node.textContent='';}
 };
}
async function sendQuestion(){
 if(busy)return;
 const text=question.value.trim();if(!text){setStatus('问题不能为空');return;}
 if(!user){setStatus('请先登录或重新登录');return;}
 addMessage('user',text);const row=addMessage('assistant');row.meta.textContent='生成中 · 草稿';
 const writer=typewriter(row.body),abort=new AbortController();controller=abort;setBusy(true);setStatus('正在判断查阅需求…',true);scrollBottom();
 let complete=false,finalAnswer='',references=[];
 try{
  const response=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json','X-ArcSync-Request':'1'},
                                    body:JSON.stringify({session_id:sessionId,question:text,allow_draft:$('#allow-draft').checked}),signal:abort.signal});
  if(!response.ok){const body=await response.json();if(response.status===401)authenticationLost();throw new Error(body.message||'请求未完成');}
  if(!response.headers.get('content-type')?.startsWith('text/event-stream'))throw new Error('服务未返回聊天流，请稍后重试');
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
  while(true){
   const part=await reader.read();buffer+=decoder.decode(part.value||new Uint8Array(),{stream:!part.done});buffer=buffer.replace(/\r\n/g,'\n');
   let end;
   while((end=buffer.indexOf('\n\n'))>=0){
    const lines=buffer.slice(0,end).split('\n');buffer=buffer.slice(end+2);
    const name=lines.find(line=>line.startsWith('event:'))?.slice(6).trim();
    if(!name)continue; // 心跳注释没有事件正文。
    const raw=lines.filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trimStart()).join('\n');
    const item=JSON.parse(raw);
    if(item.code!==0)throw new Error(item.message||'回答未完成');
    const data=item.data||{};
    if(name==='session'){sessionId=data.session_id;changeURL();}
    else if(name==='status')setStatus(item.message,true);
    else if(name==='delta')writer.append(data.text);
    else if(name==='citations')references=data.items;
    else if(name==='done'){complete=true;finalAnswer=data.answer;$('#stop').disabled=true;}
   }
   if(part.done)break;
  }
  if(!complete)throw new Error('连接中断，未完成的草稿已撤回，请重新提问');
  await writer.finish(finalAnswer);showCitations(row.article,references);row.meta.textContent='已保存 · 内容请结合档案原文核对';
  question.value='';updateCount();setStatus('回答已完成，可继续追问');
 }catch(error){
  writer.cancel();row.article.classList.add('error');row.body.textContent=error.name==='AbortError'?'已停止生成。':errorText(error);
  row.meta.textContent='未完成，草稿不计入后续上下文';setStatus(error.name==='AbortError'?'已停止生成':errorText(error));
 }finally{
  controller=null;$('#allow-draft').checked=false;setBusy(false);scrollBottom();
  if(user){try{await loadSessions();}catch(error){setStatus(errorText(error));}}
 }
}
$('#chat-form').addEventListener('submit',event=>{event.preventDefault();sendQuestion();});
question.addEventListener('input',updateCount);
question.addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();sendQuestion();}});
$('#stop').addEventListener('click',()=>controller?.abort());
$('#new-chat').addEventListener('click',newChat);
$('#refresh-sessions').addEventListener('click',()=>loadSessions().catch(error=>setStatus(errorText(error))));
$('#more-sessions').addEventListener('click',()=>loadSessions(true).catch(error=>setStatus(errorText(error))));
$('#refresh-messages').addEventListener('click',()=>{if(sessionId)openSession(sessionId).catch(error=>setStatus(errorText(error)));});
$('#older-messages').addEventListener('click',()=>openSession(sessionId,true).catch(error=>setStatus(errorText(error))));
document.querySelectorAll('[data-question]').forEach(button=>button.addEventListener('click',()=>{question.value=button.dataset.question;updateCount();question.focus();}));
window.addEventListener('pagehide',()=>controller?.abort());
window.addEventListener('focus',async()=>{
 if(!user)return;
 try{const current=await requestJSON('/api/session');if(current.user_id!==user.user_id){authenticationLost();location.reload();}}
 catch{/* 请求函数已处理失效登录，不把异常详情显示给用户。 */}
});
(async()=>{
 try{
  user=await requestJSON('/api/session');$('#user-name').textContent=user.name;$('#back-portal').href=user.home;
  const config=await requestJSON('/api/models/status');
  if(!config.configured){$('#configuration-notice').hidden=false;$('#configuration-notice').textContent='模型暂未配置，管理员完成接入后即可问答；已有会话仍可查看。';}
  await loadSessions();const requested=new URL(location.href).searchParams.get('session');
  if(requested){try{await openSession(requested);}catch(error){sessionId=null;changeURL();setStatus(errorText(error));}}
  else setStatus('描述你的问题，答案会附上档案来源');
 }catch(error){setStatus(errorText(error));}
})();
