'use strict';
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const h = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const paths = {
 archive:'M3 5h18v5H3z M5 10v11h14V10 M9 14h6 M8 2h8',
 grid:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
 search:'M21 21l-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
 file:'M14 2H5v20h14V7z M14 2v6h5 M8 12h8 M8 16h6',
 check:'M5 12l4 4L20 5',clock:'M12 8v5l3 2 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
 shield:'M12 2l8 3v6c0 5-4 9-8 11-4-2-8-6-8-11V5z M8 12l3 3 5-6',
 chart:'M4 3v18h17 M8 16v-5 M13 16V7 M18 16v-9',
 alert:'M12 3L2 21h20z M12 9v5 M12 17v1',
 arrow:'M5 12h14 M14 7l5 5-5 5', chevron:'M9 5l7 7-7 7',down:'M6 9l6 6 6-6',
 plus:'M12 5v14 M5 12h14',close:'M5 5l14 14 M19 5L5 19',
 users:'M16 21v-3a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v3 M12 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0 M17 3a4 4 0 0 1 0 8 M19 14a4 4 0 0 1 4 4v3',
 swap:'M3 7h17l-4-4 M21 17H4l4 4 M20 7l-4 4 M4 17l4-4',
 settings:'M4 7h16 M4 17h16 M8 4v6 M16 14v6',
 download:'M12 3v12 M7 10l5 5 5-5 M4 16v5h16v-5',
 refresh:'M20 7a9 9 0 1 0 1 9 M20 2v6h-6',
 chat:'M21 16a2 2 0 0 1-2 2H8l-5 4V4a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z M7 7h10 M7 12h7',
 eye:'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12 M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0',
 book:'M12 5v16 M12 5C8 2 4 3 2 4v16c4-2 7-1 10 1 3-2 6-3 10-1V4c-4-2-7-1-10 1',
 logout:'M9 3H3v18h6 M9 12h13 M17 7l5 5-5 5',
 lock:'M6 10V7a6 6 0 0 1 12 0v3 M4 10h16v12H4z M12 15v3',
 inbox:'M4 3h16v18H4z M4 14h5l2 3h2l2-3h5 M8 7h8 M8 10h6',
 pause:'M8 5v14 M16 5v14'
};
const icon = (name, cls = '') => `<svg class="icon ${cls}" aria-hidden="true" viewBox="0 0 24 24"><path d="${paths[name] || paths.file}"/></svg>`;
const navigation = [
 ['dashboard','工作台','grid'], ['businesses','查阅业务','file'], ['search','档案检索','search'],
 ['reviews','审核与交接','swap'], ['issues','异常与修复','alert'], ['metrics','试点评估','chart'],
 ['feedback','服务反馈','chat'], ['settings','管理与审计','settings']
];
let S=null, current='dashboard', selectedCase=null, taskTab='mine', businessFilter='', issueFilter='open';
let searchResults=[], searchQuery='', filters={category:'',year:'',status:''}, searchSeq=0;
let modalContext=null, pageData=null, toastTimer, lastFocus, busy=false;
const roleName = r => S?.roles.find(x => x.id===r)?.label || r;
const rolePerson = r => S?.roles.find(x => x.id===r)?.name || r;
const owned = b => b.owner === S.user.role;
const hasRole = (...r) => r.includes(S.user.role);
const businessById = id => S.businesses.find(b=>b.id===id);
const date = (value, full=false) => value ? new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',...(full?{year:'numeric'}:{})}).format(new Date(value)) : '尚未记录';
const badge = (s) => `<span class="badge ${/异常|待修复|已更正|已暂停/.test(s)?'red':/待审核|待复核|待接手|待核对|待权限|待分派|待处理/.test(s)?'amber':/定位中/.test(s)?'blue':/已交付|已处理|已接手|已核对/.test(s)?'gray':''}">${h(s)}</span>`;
const btn = (label, action, attrs='', type='', ico='') => `<button type="button" class="btn ${type}" data-action="${action}" ${attrs}>${ico?icon(ico):''}${label}</button>`;
const empty = (title, detail='当前没有待处理记录', ico='inbox') => `<div class="empty">${icon(ico)}<strong>${h(title)}</strong>${h(detail)}</div>`;
let fieldSerial=0;
const field = (label,name,placeholder='',value='',type='text',required=true) => {const uid=`f-${name}-${++fieldSerial}`;return `<div class="field"><label for="${uid}">${h(label)}</label><input class="input" id="${uid}" name="${name}" type="${type}" placeholder="${h(placeholder)}" value="${h(value)}" ${required?'required':''}></div>`;};
const textarea = (label,name,placeholder='',value='',required=true) => {const uid=`f-${name}-${++fieldSerial}`;return `<div class="field"><label for="${uid}">${h(label)}</label><textarea id="${uid}" name="${name}" placeholder="${h(placeholder)}" ${required?'required':''}>${h(value)}</textarea></div>`;};
const select = (label,name,options,value='') => {const uid=`f-${name}-${++fieldSerial}`;return `<div class="field"><label for="${uid}">${h(label)}</label><select id="${uid}" name="${name}">${options.map(([id,title])=>`<option value="${h(id)}" ${id===value?'selected':''}>${h(title)}</option>`).join('')}</select></div>`;};
async function api(path, data) {
  let response;
  try { response=await fetch(`/api/${path}`, data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-ArcSync-Request':'1'},body:JSON.stringify(data)}); }
  catch { throw new Error('连接暂时中断，请检查本地服务；现有人工流程可继续使用'); }
  const result=await response.json();
  if (!response.ok) {
    if(response.status===401 && path!=='login') { S=null; closeModal(); renderLogin(); }
    throw new Error(result.error || '请求未完成');
  }
  return result;
}
function toast(message, error=false) {
  clearTimeout(toastTimer);const el=$('#toast');el.textContent=message;el.className=`visible${error?' error':''}`;
  toastTimer=setTimeout(()=>el.className='',4300);
}
async function refresh() { S=await api('state'); }
function renderLogin(error='') {
  $('#app').innerHTML=`<div class="login"><section class="login-story"><div class="brand"><div class="brandmark">${icon('archive')}</div><div><strong>云州档案</strong><small>YUNZHOU ARCHIVES</small></div></div><h1>让每一次查阅，<br>都有据可循。</h1><p>从档案检索到卷页核对，把原卷依据、人工判断与责任交接连接在一起。</p><div class="login-art">${archiveArt()}</div><footer>ARCSYNC / 档案检索与查阅协同平台 · 本地演示版</footer></section><section class="login-form-wrap"><form class="login-form" data-form="login"><div class="eyebrow">WELCOME TO ARCSYNC</div><h2>进入档案工作台</h2><p>选择业务岗位，开始一次有依据的查阅。</p><div class="notice sage">${icon('shield')}<span>当前为合成数据演示环境，不含真实档案。岗位切换用于验证不同权限与业务流程。</span></div>${select('当前岗位','role', [['organizer','林亦舟 · 档案整理员'],['reviewer','唐映秋 · 查阅审核员'],['protector','傅景 · 档案保护专员'],['engineer','秦芮 · 档案系统工程师'],['director','顾明岚 · 档案服务主任'],['representative','叶书 · 业务查询代表']])}${field('演示口令','password','输入本地演示口令','ArcSync2026!','password')}<div class="error-inline">${h(error)}</div><button class="btn primary full" type="submit">进入工作台 ${icon('arrow')}</button><div class="login-caption">默认演示口令：<span class="mono">ArcSync2026!</span><br>登录有效期 8 小时。真实身份认证与业务授权需在生产接入时配置。</div></form></section></div>`;
}
function archiveArt(){return '<div class="hero-circle"></div><div class="archive-drawing"><div class="archive-label">档案<small>YUNZHOU · ARCHIVES</small></div></div>';}
function sidebar(){
 const pending=S.businesses.filter(b=>owned(b)&&b.status!=='已交付').length;
 const issues=S.issues.filter(i=>i.status!=='已处理').length;
 const handoffs=S.handoffs.filter(x=>x.receiver===S.user.role&&x.status==='待接手').length;
 return `<aside class="sidebar"><a class="brand" href="#dashboard" aria-label="云州档案工作台"><div class="brandmark">${icon('archive')}</div><div><strong>云州档案</strong><small>YUNZHOU ARCHIVES</small></div></a><div class="nav-label">业务工作空间</div><nav class="nav">${navigation.map(([id,title,i],n)=>`${n===5?'<div class="nav-label">评估与管理</div>':''}<a href="#${id}" class="${current===id||current==='detail'&&id==='businesses'?'active':''}" ${current===id?'aria-current="page"':''} title="${title}">${icon(i)}<span>${title}</span>${id==='dashboard'&&pending?`<span class="nav-count">${pending}</span>`:id==='issues'&&issues?`<span class="nav-count">${issues}</span>`:id==='reviews'&&handoffs?`<span class="nav-count">${handoffs}</span>`:''}</a>`).join('')}</nav><div class="sidebar-bottom"><div class="pilot-card"><strong>有边界的智能辅助</strong><p>每一条建议都关联原卷依据<br>每一次交接都有明确责任</p><span>公开档案 · 小范围试点 ${icon('arrow')}</span></div><div class="sidebar-foot"><span class="dot"></span> 本地服务运行中 · v0.1.0</div></div></aside>`;
}
function shell(content){
 const name=navigation.find(n=>n[0]===current)?.[1] || '业务详情';
 $('#app').innerHTML=`<div class="shell">${sidebar()}<div class="workspace"><header class="topbar"><div class="crumb"><span>业务工作空间</span>${icon('chevron')}<strong>${name}</strong></div><div class="top-right"><button class="icon-btn" data-action="refresh" aria-label="刷新业务数据" title="刷新业务数据">${icon('refresh')}</button><span class="mode-tag">合成数据演示</span><span class="header-date">${new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'long',day:'numeric',weekday:'long'}).format(new Date())}</span><button class="profile" data-action="role" title="切换演示岗位"><span class="avatar">${h(S.user.name.slice(-2))}</span><span><strong>${h(S.user.name)}</strong><small>${h(S.user.label)}</small></span>${icon('down')}</button></div></header><main class="main">${S.settings.paused==='true'?`<div class="notice red">${icon('pause')}<span>试点已暂停。自动推荐和正常流程推进已停止，可继续查看、保存草稿和处理人工异常。</span></div>`:''}${S.settings.stale?`<div class="notice">${icon('clock')}<span>数据暂未更新。当前为 ${date(S.settings.sync_at,true)} 的只读来源快照，不能作为实时承诺。关键依据请转人工复核。</span></div>`:''}${content}<footer class="footer"><span>© 2026 云州档案服务中心 · ARCSYNC</span><span>合成数据演示 · 原卷须人工确认 · 不改变开放范围与档案处置</span></footer></main></div></div>`;
}
function heading(title,desc,actions='',eyebrow=''){return `<div class="page-heading"><div>${eyebrow?`<div class="eyebrow">${eyebrow}</div>`:''}<h1>${title}</h1><p>${desc}</p></div>${actions?`<div class="button-row">${actions}</div>`:''}</div>`;}
function stat(label,num,unit,foot,ico='file',warn=false){return `<div class="stat"><div class="stat-head">${label}<span class="stat-icon">${icon(ico)}</span></div><div class="stat-num">${num}<em>${unit}</em></div><div class="stat-foot ${warn?'warn':''}">${foot}</div></div>`;}
function caseTable(cases,compact=false){
 if(!cases.length)return empty('暂无匹配业务','可以切换筛选条件，或新建一笔查阅业务');
 return `<div class="table-wrap"><table><thead><tr><th>查阅事项 / 业务编号</th>${compact?'':'<th>申请单位</th>'}<th>当前状态</th><th>责任岗位</th><th>操作</th></tr></thead><tbody>${cases.map(b=>`<tr><td><a class="case-title" href="#detail/${h(b.id)}">${h(b.title)}</a><span class="case-id">${h(b.id)}${b.sample?' · 样例':''}</span></td>${compact?'':`<td>${h(b.applicant)}</td>`}<td>${badge(b.status)}</td><td><span>${h(rolePerson(b.owner))}</span></td><td><a class="link" href="#detail/${h(b.id)}">${owned(b)&&b.status!=='已交付'?'去处理':'查看'} ${icon('chevron')}</a></td></tr>`).join('')}</tbody></table></div>`;
}
function dashboard(){
 const mine=S.businesses.filter(b=>owned(b)&&b.status!=='已交付');
 const incoming=S.handoffs.filter(h=>h.receiver===S.user.role&&h.status==='待接手');
 const issues=S.issues.filter(i=>i.status!=='已处理');
 const done=S.businesses.filter(b=>b.status==='已交付').length;
 const shown=taskTab==='mine'?mine:taskTab==='handoff'?S.businesses.filter(b=>incoming.some(h=>h.business_id===b.id)):S.businesses.filter(b=>b.status!=='已交付');
 return `${heading(`上午好，${h(S.user.name)}。`.replace('上午好',greeting()),'从一份原卷、一条依据，开始今天的档案服务。',(hasRole('organizer','reviewer','representative')?btn('新建查阅申请','create','','primary','plus'):''),'YOUR ARCHIVE WORKSPACE')}
 <section class="hero"><div><small>查得准确 · 交得清楚 · 全程可溯</small><h2>让检索的每一步，都有原卷依据</h2><p>在授权范围内找到候选档案，核对卷内页码与更正关系，<br>将已确认的依据和待办事项一起交到下一位同事手中。</p><div class="button-row"><a class="btn primary" href="#search">${icon('search')} 开始档案检索</a><a class="btn text" href="#reviews">查看审核与交接 ${icon('arrow')}</a></div></div><div class="hero-art">${archiveArt()}</div></section>
 <section class="stats">${stat('我的待办',mine.length,'笔','<b>当前责任</b> 待确认接手后转移','inbox')}${stat('待接手交接',incoming.length,'笔','<b>确认接手</b> 才完成责任交接','swap')}${stat('未解决异常',issues.length,'项','<b>人工处理</b> 保留触发原因与依据','alert',true)}${stat('已交付业务',done,'笔','本地演示数据 · 不计试点成果','check')}</section>
 <div class="dashboard-grid"><div><section class="panel"><div class="panel-head"><h2>业务待办 <small>按业务编号统一追踪</small></h2><a href="#businesses">全部业务 ${icon('arrow')}</a></div><div class="tabs">${[['mine','我的待办',mine.length],['handoff','待我接手',incoming.length],['all','全部进行中',S.businesses.length-done]].map(([id,label,n])=>`<button class="${taskTab===id?'active':''}" data-action="task-tab" data-tab="${id}">${label}<span>${n}</span></button>`).join('')}</div>${caseTable(shown.slice(0,5),true)}<div class="table-footer"><span>共 ${shown.length} 笔业务 · 每笔业务独立留痕</span><span>业务记录 ${icon('shield')}</span></div></section><section class="panel"><div class="panel-head"><div><h2>查阅协作流程</h2><p>依据随单流转，异常有明确接手人</p></div><span class="badge">人工审核把关</span></div>${flow()}<div class="flow-foot">${icon('shield')} 系统辅助定位与核对，最终审核和交付由授权人员确认。</div></section></div><aside class="dashboard-aside"><section class="panel"><div class="panel-head"><h2>需要关注</h2><a href="#issues">${icon('arrow')}</a></div>${issues.length?issues.slice(0,2).map(i=>`<a href="#issues" class="aside-item"><span class="aside-icon">${icon('alert')}</span><div><strong>${h(i.kind)}</strong><p>${h(i.detail)}</p><small>${h(roleName(i.owner))} · ${h(i.status)}</small></div></a>`).join(''):empty('暂无未解决异常','继续保持依据完整','check')}<div class="aside-item"><span class="aside-icon">${icon('shield')}</span><div><strong>引用准确率目标 99%</strong><p>指标须经历史回放和人工复核验证，当前尚未形成试点评估样本。</p><small>试点目标 · 非实测成绩</small></div></div></section>${systemPanel()}</aside></div>`;
}
function greeting(){const hr=Number(new Intl.DateTimeFormat('en-US',{timeZone:'Asia/Shanghai',hour:'numeric',hour12:false}).format(new Date()));return hr<12?'上午好':hr<18?'下午好':'晚上好';}
function flow(status=''){const names=['查阅申请','权限核验','档案检索','卷内定位','人工审核','记录交付'];const icons=['file','shield','search','book','check','inbox'];let idx={'待权限核验':1,'定位中':3,'待审核':4,'待复核':4,'待交付':5,'已交付':5}[status];return `<div class="mini-steps">${names.map((n,i)=>`<div class="mini-step ${(idx===undefined?i===3:i<=idx)?'highlight':''}"><span class="step-icon">${icon(icons[i])}</span>${n}</div>`).join('')}</div>`;}
function systemPanel(){return `<section class="panel"><div class="panel-head"><h2>数据连接状态</h2><span class="dot"></span></div><div class="system-list">${[['档案管理库','待接入'],['数字化 OCR','待接入'],['授权审核台','待接入'],['本地样例数据','可用']].map(([a,b])=>`<div class="system-row"><span>${a}</span><span>${b}</span></div>`).join('')}</div><div class="system-foot">${icon('clock')} 本地快照：${date(S.settings.sync_at)}<br>真实接口待确认 · 业务状态计划每小时更新</div></section>`;}
function businesses(){
 const list=S.businesses.filter(b=>!businessFilter||b.status===businessFilter);
 return `${heading('查阅业务','一笔业务，一个编号。原卷依据、处理说明与交接记录始终关联。',(hasRole('organizer','reviewer','representative')?btn('新建查阅申请','create','','primary','plus'):''),'BUSINESS RECORDS')}<form class="filter-bar" data-form="business-filter"><div class="search-input">${icon('search')}<input class="input" name="q" id="business-search" placeholder="按业务名称、业务编号或申请单位搜索" aria-label="搜索业务"></div><select name="status" id="business-status" aria-label="业务状态"><option value="">全部状态</option>${['待权限核验','定位中','待审核','异常处理中','待复核','待交付','已交付'].map(s=>`<option ${businessFilter===s?'selected':''}>${s}</option>`).join('')}</select><button class="btn primary" type="submit">筛选业务</button></form><section class="panel"><div class="panel-head"><h2>业务台账 <small id="business-count">${list.length} 笔</small></h2><span class="sample-note">样例与正式验证样本分开统计</span></div><div id="business-table">${caseTable(list)}</div></section>`;
}
function search(){
 const categories=[...new Set(S.documents.map(d=>d.category))];const years=[...new Set(S.documents.map(d=>d.year))].sort((a,b)=>b-a);
 return `${heading('档案检索','在授权范围内查找档案，回到原卷核对每一条引用。',btn('检索帮助','help','','','book'),'SEARCH & VERIFY')}<form class="filter-bar" data-form="search"><div class="search-input">${icon('search')}<input class="input" name="q" value="${h(searchQuery)}" placeholder="输入档案标题、档号或关键词，如：总体规划" aria-label="档案检索关键词"></div><select name="category" aria-label="档案类别"><option value="">全部类别</option>${categories.map(x=>`<option ${filters.category===x?'selected':''}>${h(x)}</option>`).join('')}</select><select name="year" aria-label="形成年度"><option value="">全部年度</option>${years.map(x=>`<option ${filters.year===String(x)?'selected':''}>${x}</option>`).join('')}</select><select name="status" aria-label="资料状态"><option value="">全部状态</option>${['有效','已更正','待修复'].map(x=>`<option ${filters.status===x?'selected':''}>${x}</option>`).join('')}</select><button type="submit" class="btn primary search-submit">${icon('search')} 检索</button></form><div class="result-layout"><div><div class="result-meta"><span id="result-count">正在查询授权目录…</span><span>目录与固定摘要匹配 · 未接入 AI</span></div><div id="search-results"><div class="loading">正在读取授权档案…</div></div></div><aside><section class="panel"><div class="panel-head"><h2>${icon('shield')} 本次查阅范围</h2></div><div class="side-content"><span class="badge">${hasRole('protector')?'公开与专员授权资料':'一类公开档案'}</span><h3>先核权限，再展示内容</h3><p>检索结果与卷页预览均由服务端校验。无权限资料不会进入候选结果或摘要。</p><div class="kv"><span>当前岗位</span><strong>${h(S.user.label)}</strong></div><div class="kv"><span>资料来源</span><strong>合成演示资料</strong></div><div class="kv"><span>写入原档案</span><strong>关闭</strong></div></div></section><section class="panel"><div class="panel-head"><h2>引用核对清单</h2></div><div class="side-content"><ol class="rule-list">${['核对档号、资料版本与原卷内容','区分扫描页序号与纸质页码','检查后续更正与适用范围','由整理员确认后加入业务依据'].map((s,i)=>`<li><span class="rule-number">${i+1}</span><span>${s}</span></li>`).join('')}</ol><div class="notice sage mt">${icon('eye')}<span>资料缺少依据、页码不符或版本冲突时，请暂停建议并转人工。</span></div></div></section></aside></div>`;
}
async function performSearch(){
 const seq=++searchSeq;
 try{
 const qs=new URLSearchParams({q:searchQuery,...filters});const r=await api(`search?${qs}`);
 if(seq!==searchSeq||current!=='search')return;
 searchResults=r.documents;
 $('#result-count').textContent=`找到 ${searchResults.length} 份授权档案${searchQuery?` · “${searchQuery}”`:''}`;
 $('#search-results').innerHTML=searchResults.length?searchResults.map(docCard).join(''):empty('没有找到匹配档案','试试缩短关键词，或清除类别、年度和状态筛选','search');
 }catch(e){if(current==='search'&&$('#search-results'))$('#search-results').innerHTML=empty('检索暂未完成',e.message,'alert');}
}
function docCard(d){return `<article class="doc-card"><div class="doc-file">${icon('file')}</div><div class="doc-body"><div class="doc-top"><button class="doc-title" data-action="read" data-id="${h(d.id)}">${h(d.title)}</button>${badge(d.status)}</div><div class="doc-meta"><span>${h(d.id)}</span><span>${d.year} 年</span><span>${h(d.category)}</span><span>${d.pages} 页</span><span>${h(d.version)}</span><span>${h(d.level)}</span></div><p class="doc-summary">${h(d.summary)}</p>${d.correction?`<div class="doc-correction">${icon('swap')} ${d.status==='已更正'?'此版本已更正，须查阅后续文件':'存在后续更正，引用前须核对'}：${h(d.correction)}</div>`:''}${d.status==='待修复'?'<div class="doc-correction">页码或日期存在歧义，已从自动推荐样本中排除。</div>':''}<div class="doc-bottom"><span class="doc-source">${h(d.match_reason||'授权目录')} · 合成资料</span>${btn('打开原卷 · 核对卷页','read',`data-id="${h(d.id)}"`,'small', 'book')}</div></div></article>`;}
function eventDetail(e){
 if(e.action==='人工确认卷页引用'&&e.detail.startsWith('{')){
  try{const c=JSON.parse(e.detail);return `${c.document_id} · ${c.version} · 扫描第 ${c.scan_page} 页 → 纸质第 ${c.paper_page} 页；已人工核对${c.correction_checked?'并检查更正关系':''}。`;}catch{}
 }
 return e.detail;
}
function detail(){
 const b=businessById(selectedCase);
 if(!b)return heading('业务不存在','该记录可能已更新，请返回业务列表。','<a class="btn" href="#businesses">返回业务列表</a>');
 const events=S.events.filter(e=>e.business_id===b.id);
 const handoffs=S.handoffs.filter(x=>x.business_id===b.id);
 let actions='';
 if(owned(b)){
 if(hasRole('reviewer')&&b.status==='待权限核验')actions+=btn('核验试点权限','authorize',`data-id="${h(b.id)}"`,'primary','shield');
 if(hasRole('organizer')&&b.status==='定位中')actions+='<a class="btn primary" href="#search">'+icon('search')+' 检索并确认依据</a>'+btn('提交审核','submit',`data-id="${h(b.id)}"`,'','check');
 if(hasRole('reviewer')&&['待审核','待复核'].includes(b.status))actions+=btn('审核通过','approve',`data-id="${h(b.id)}"`,'primary','check')+btn('退回定位','return',`data-id="${h(b.id)}"`,'','swap');
 if(hasRole('reviewer')&&b.status==='待复核')actions+=btn('复核后恢复定位','resume',`data-id="${h(b.id)}"`,'','refresh');
 if(hasRole('reviewer')&&b.status==='待交付')actions+=btn('记录人工交付','deliver',`data-id="${h(b.id)}"`,'primary','check');
 }
 if(b.status==='已交付')actions+=btn('导出交付记录','export',`data-id="${h(b.id)}"`,'','download');
 const incoming=handoffs.find(x=>x.receiver===S.user.role&&x.status==='待接手');
 return `${heading(h(b.title),`${h(b.id)} · ${h(b.applicant)}${b.sample?' · 合成业务样例':''}`,'<a class="btn" href="#businesses">返回台账</a>','BUSINESS DETAIL')}${incoming?`<div class="notice sage">${icon('swap')}<span>此业务等待您确认接手。交出方：${h(rolePerson(incoming.sender))}。${h(incoming.note)}<br>${btn('确认接手','accept',`data-id="${incoming.id}"`,'small primary')}</span></div>`:''}<section class="panel"><div class="panel-head"><h2>业务进度 ${badge(b.status)}</h2><span class="muted">当前责任：${h(rolePerson(b.owner))} · ${h(roleName(b.owner))}</span></div>${flow(b.status)}</section><div class="detail-grid"><div><section class="panel"><div class="panel-head"><h2>查阅信息与引用依据</h2><span class="muted">记录版本 r${b.revision}</span></div><div class="detail-body"><dl class="info-grid"><div><dt>申请单位</dt><dd>${h(b.applicant)}</dd></div><div><dt>查阅用途</dt><dd>${h(b.purpose)}</dd></div><div><dt>创建时间</dt><dd>${date(b.created,true)}</dd></div><div><dt>最后更新</dt><dd>${date(b.updated,true)}</dd></div></dl>${b.return_reason?`<div class="notice">${icon('swap')}<span>退回原因：${h(b.return_reason)}</span></div>`:''}<div class="section-title">已确认卷页引用 <span class="demo-label">${b.citations.length} 条</span></div>${b.citations.length?b.citations.map(c=>`<div class="citation">${btn('查看卷页','read',`data-id="${h(c.document_id)}" data-page="${c.scan_page}"`,'small','eye')}<strong>${h(S.documents.find(d=>d.id===c.document_id)?.title||c.document_id)}</strong><br><span class="mono">${h(c.document_id)}</span> · ${h(c.version)}<br>扫描第 ${c.scan_page} 页 → 纸质第 ${c.paper_page} 页<br><span class="muted">${h(c.by||'合成样例')} · 人工确认${c.correction_checked?' · 已核对更正关系':''}</span></div>`).join(''):empty('尚无已确认依据','整理员打开候选卷页，核对后将引用加入此业务','book')}<div class="button-row mt">${actions}</div></div></section><section class="panel"><div class="panel-head"><h2>处理时间线</h2><span class="muted">输入、判断与交接均可追溯</span></div><div class="timeline">${events.map(e=>`<div class="timeline-item"><strong>${h(e.action)}</strong><small>${h(e.actor)}</small><p>${h(eventDetail(e))}</p><time>${date(e.at,true)}</time></div>`).join('')}</div></section></div><aside><section class="panel"><div class="panel-head"><h2>随单说明与草稿</h2>${icon('file')}</div><form class="side-content" data-form="note" data-id="${h(b.id)}">${textarea('关键说明 / 待确认事项','note','把个人便签中的业务说明留在这里，交接时同事也能看到。',b.note,false)}${owned(b)&&b.status!=='已交付'?'<button class="btn full" type="submit">保存草稿</button>':'<p class="muted">仅当前责任岗位可修改未交付业务的草稿。</p>'}<p class="muted">最后保存：${date(b.updated)}<br>草稿保存到本地服务，不覆盖原档案。</p></form></section><section class="panel"><div class="panel-head"><h2>责任交接与异常</h2></div><div class="side-content">${handoffs.map(x=>`<div class="citation">${badge(x.status)}<br>${h(rolePerson(x.sender))} → ${h(rolePerson(x.receiver))}<br>${h(x.note)}<br><span class="muted">${date(x.created)}</span></div>`).join('')}${owned(b)&&b.status!=='已交付'?btn('发起责任交接','handoff',`data-id="${h(b.id)}"`,'full','swap'):''}${hasRole('organizer','reviewer','engineer','protector')&&b.status!=='已交付'?btn('暂停建议 · 转人工','issue',`data-id="${h(b.id)}"`,'full danger mt','alert'):''}<p class="muted">发出交接后，待接手人确认，原责任岗位的待办才结束。异常先由审核员分派。</p></div></section></aside></div>`;
}
function reviews(){
 const incoming=S.handoffs.filter(x=>x.status==='待接手'&&x.receiver===S.user.role);
 const reviews=S.businesses.filter(b=>['待审核','待复核','待权限核验','待交付'].includes(b.status));
 return `${heading('审核与交接','确认接手后再处理，让责任在每一个节点都有明确归属。','','REVIEW & HANDOFF')}<section class="stats">${stat('待我接手',incoming.length,'笔','接手确认前，责任仍在交出方','swap')}${stat('等待审核',S.businesses.filter(b=>b.status==='待审核').length,'笔','人工确认依据与适用规则','shield')}${stat('等待权限核验',S.businesses.filter(b=>b.status==='待权限核验').length,'笔','首期仅核验公开档案试点','lock')}${stat('等待交付',S.businesses.filter(b=>b.status==='待交付').length,'笔','审核通过后记录人工交付','inbox')}</section><section class="panel"><div class="panel-head"><h2>待我确认接手</h2><span class="muted">${h(S.user.label)}</span></div>${incoming.length?`<div class="table-wrap"><table><thead><tr><th>业务编号</th><th>交出人</th><th>未解决事项 / 交接说明</th><th>操作</th></tr></thead><tbody>${incoming.map(x=>`<tr><td><a class="link" href="#detail/${h(x.business_id)}">${h(x.business_id)}</a></td><td>${h(rolePerson(x.sender))}</td><td class="break">${h(x.note)}</td><td>${btn('确认接手','accept',`data-id="${x.id}"`,'small primary')}</td></tr>`).join('')}</tbody></table></div>`:empty('暂无等待您接手的交接','同事提交交接后，会在这里显示','swap')}</section><section class="panel"><div class="panel-head"><h2>审核与交付台账</h2><span class="muted">审核操作仅向当前负责的审核员开放</span></div>${caseTable(reviews)}</section><section class="panel"><div class="panel-head"><h2>全部交接记录</h2></div>${S.handoffs.length?`<div class="table-wrap"><table><thead><tr><th>业务编号</th><th>交出 → 接手</th><th>状态</th><th>确认时间</th></tr></thead><tbody>${S.handoffs.map(x=>`<tr><td><a href="#detail/${h(x.business_id)}" class="link">${h(x.business_id)}</a></td><td>${h(rolePerson(x.sender))} → ${h(rolePerson(x.receiver))}</td><td>${badge(x.status)}</td><td>${x.accepted?date(x.accepted):'等待接手确认'}</td></tr>`).join('')}</tbody></table></div>`:empty('还没有交接记录','可从业务详情发起交接')}</section>`;
}
function issues(){
 const list=S.issues.filter(i=>issueFilter==='all'||(issueFilter==='open'?i.status!=='已处理':i.status==='已处理'));
 return `${heading('异常与数据修复','先暂停有疑问的建议，再由具名责任人核对。修复说明不会覆盖原记录。','','EXCEPTIONS & REPAIR')}<div class="notice sage">${icon('shield')}<span>页码歧义、日期错读、版本冲突和缺少依据的资料不进入自动推荐样本。异常处理后，须由审核员复核恢复。</span></div><section class="panel"><div class="panel-head"><h2>人工处理队列 <small>${S.issues.length} 项记录</small></h2></div><div class="tabs">${[['open','待处理'],['done','已处理'],['all','全部记录']].map(([id,label])=>`<button data-action="issue-tab" data-tab="${id}" class="${issueFilter===id?'active':''}">${label}</button>`).join('')}</div></section>${list.length?`<div class="issue-list">${list.map(i=>`<article class="issue-card"><div class="button-row">${badge(i.status)}<span class="muted">异常 #${String(i.id).padStart(3,'0')}</span></div><h3>${h(i.kind)}</h3><p>${h(i.detail)}</p><div class="meta">业务编号：<a class="link" href="#detail/${h(i.business_id)}">${h(i.business_id)}</a><br>关联档案：${h(i.document_id||'业务级异常')}<br>处理岗位：${h(roleName(i.owner))} · ${h(rolePerson(i.owner))}<br>触发时间：${date(i.created,true)}</div>${i.resolution?`<div class="resolution">处理说明：${h(i.resolution)}<br>源档案状态须经独立修复与重新校验。</div>`:''}<div class="button-row mt">${i.status!=='已处理'&&i.owner===S.user.role?btn('填写处理结果','resolve',`data-id="${i.id}"`,'primary small','check'):''}${i.status!=='已处理'&&hasRole('reviewer')?btn('分派处理人','assign',`data-id="${i.id}"`,'small','users'):''}${i.document_id?btn('查看关联卷页','read',`data-id="${h(i.document_id)}"`,'small','book'):''}</div></article>`).join('')}</div>`:empty('没有匹配的异常记录','可以切换处理状态查看其他记录','check')}`;
}
function metrics(){
 const real=S.businesses.filter(b=>!b.sample), completed=real.filter(b=>b.status==='已交付');
 const stages=['待权限核验','定位中','待审核','异常处理中','待复核','待交付','已交付'];
 return `${heading('试点评估','按固定业务编号统计，分别记录处理与等待，避免重复计成功。','','PILOT EVALUATION')}<div class="notice">${icon('alert')}<span>当前未接入真实试点样本，页面仅展示流程运行数据。合成样例不计入业务效果。50 分钟、6% 为访谈方向锚点，尚未核实为基线。</span></div><section class="stats">${stat('新建演示业务',real.length,'笔','不含预置样例 · 不等于试点样本','file')}${stat('新建业务已交付',completed.length,'笔','按唯一业务编号去重统计','check')}${stat('引用页码准确率','—','','目标 <b>≥ 99%</b> · 待独立人工复核','book')}${stat('受限内容泄露','—','','目标 <b>零泄露</b> · 待专项测试验证','shield')}</section><div class="metric-grid"><section class="panel"><div class="panel-head"><h2>业务状态分布</h2><span class="muted">含预置合成样例</span></div><div class="metric-content">${stages.map(s=>{const n=S.businesses.filter(b=>b.status===s).length;return `<div class="chart-row"><span>${s}</span><div class="bar"><span data-bar="${Math.round(n/Math.max(1,S.businesses.length)*100)}"></span></div><span>${n}</span></div>`}).join('')}<p class="muted">此分布用于观察工作队列，不代表试点改善成果。</p></div></section><section class="panel"><div class="panel-head"><h2>业务目标与验收前置</h2></div><div class="metric-content">${[['有效材料定位时长','访谈 50 分钟 → 目标 15 分钟'],['错卷率','访谈 6% → 目标 1%'],['引用页码准确率','目标 ≥99%，须独立复核'],['权限边界','专项测试零泄露，异常即暂停'],['统计口径','分母、排除规则、周期待确认'],['端到端时长','同步观察等待与处理，不恶化']].map(([k,v])=>`<div class="kv"><span>${k}</span><strong>${v}</strong></div>`).join('')}<p class="muted">未配置金标准样本与人工判定前，不计算准确率和时长改善百分比。</p></div></section></div><section class="panel"><div class="panel-head"><h2>节点事件原始记录</h2><span class="muted">支持后续复算各岗等待及处理时长</span></div><div class="table-wrap"><table><thead><tr><th>业务编号</th><th>事件</th><th>处理人</th><th>记录时间</th></tr></thead><tbody>${S.events.slice(0,20).map(e=>`<tr><td><a class="link" href="#detail/${h(e.business_id)}">${h(e.business_id)}</a></td><td>${h(e.action)}</td><td>${h(e.actor)}</td><td>${date(e.at,true)}</td></tr>`).join('')}</tbody></table></div></section>`;
}
function feedback(){return `${heading('服务反馈','把每一条反馈关联到原业务和当时使用的资料版本，再核对、再答复。',(hasRole('organizer','reviewer','representative')?btn('登记服务反馈','feedback','','primary','plus'):''),'SERVICE FEEDBACK')}<div class="notice sage">${icon('chat')}<span>业务查询代表受理、查阅审核员核对。未经确认，不承诺投诉成立、赔偿或处理结果。</span></div>${S.feedback.length?`<div class="issue-list">${S.feedback.map(f=>`<article class="issue-card">${badge(f.status)}<h3><a class="link" href="#detail/${h(f.business_id)}">${h(f.business_id)}</a></h3><p>${h(f.content)}</p><div class="meta">资料版本：${h(f.version)}<br>受理时间：${date(f.created,true)}</div>${f.reply?`<div class="resolution">审核核对：${h(f.reply)}</div>`:''}${f.status!=='已核对'&&hasRole('reviewer')?btn('填写核对结果','reply',`data-id="${f.id}"`,'small primary','check'):''}</article>`).join('')}</div>`:empty('暂无服务反馈','登记时请提供原业务编号与当时使用的资料版本','chat')}`;}
function settingsView(){
 const privileged=hasRole('protector','director');
 return `${heading('管理与审计','保持授权边界可见，让每次建议、人工修改和签核能够被重建。','','GOVERNANCE & AUDIT')}<div class="metric-grid"><section class="panel"><div class="panel-head"><h2>试点运行控制</h2>${badge(S.settings.paused==='true'?'已暂停':'运行中')}</div><div class="side-content"><div class="kv"><span>规则版本</span><strong>${h(S.settings.rule_version)}</strong></div><div class="kv"><span>原系统回写</span><strong>未开放</strong></div><div class="kv"><span>评价记录留存建议</span><strong>90 天 · 到期由保护专员复核</strong></div><p class="muted">真实期限按组织规则确认。本地版保留全部记录，不自动删除档案或评价数据。</p>${privileged?btn(S.settings.paused==='true'?'复盘后恢复试点':'暂停试点',S.settings.paused==='true'?'unpause':'pause','','danger','pause'):'<p class="muted">暂停控制仅对主任与保护专员开放，恢复须由保护专员确认。</p>'}</div></section><section class="panel"><div class="panel-head"><h2>接口与快照</h2></div><div class="side-content"><div class="kv"><span>本地快照时间</span><strong>${date(S.settings.sync_at,true)}</strong></div><div class="kv"><span>业务更新计划</span><strong>每小时 · 真实接口待接入</strong></div><div class="kv"><span>原始资料更新</span><strong>按版本更新</strong></div><p class="muted">当前刷新仅更新本地合成数据快照，不代表已同步外部系统。接口适配层预留，接入须先核对读写清单与权限。</p>${hasRole('engineer')?btn('刷新本地合成快照','sync','','','refresh'):''}</div></section></div><section class="panel"><div class="panel-head"><h2>岗位与权限边界</h2><span class="muted">演示权限矩阵</span></div><div class="table-wrap"><table><thead><tr><th>岗位</th><th>主要操作</th><th>权限约束</th></tr></thead><tbody>${[
 ['档案整理员','检索、原卷核对、引用确认、提交审核','仅已授权公开档案，不改原记录'],['查阅审核员','公开试点权限核验、审核、退回、异常分派','不能代替保护专员专业签核'],['档案保护专员','专员授权资料核验、风险审计、暂停与恢复','不自动改变开放范围和密级'],['档案系统工程师','处理数据异常、刷新本地快照','不审批业务，不自行开放写接口'],['档案服务主任','查看管理状态、审计、暂停试点','管理权限不扩大档案查看范围'],['业务查询代表','发起查阅、受理反馈、查看进度','不判定开放范围和原卷页码']].map(r=>`<tr>${r.map(c=>`<td class="break">${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div></section><section class="panel"><div class="panel-head"><h2>操作审计记录</h2><span class="muted">追加记录 · 本地数据库禁止更新及删除</span></div>${privileged?S.audit.length?`<div class="table-wrap"><table><thead><tr><th>时间 / 操作人</th><th>操作</th><th>业务编号</th><th>审计详情</th></tr></thead><tbody>${S.audit.map(a=>`<tr><td><span class="case-title">${h(a.actor)} · ${h(roleName(a.role))}</span><span class="case-id">${date(a.at,true)}</span></td><td>${h(a.action)}</td><td class="mono">${h(a.business_id||'系统级')}</td><td class="break"><details><summary>查看凭证</summary><div class="audit-detail">${h(JSON.stringify(JSON.parse(a.detail),null,2))}</div></details></td></tr>`).join('')}</tbody></table></div>`:empty('暂无审计记录'):empty('当前岗位无权查看全局审计','业务处理记录可以在各业务详情中查看','lock')}</section>`;
}
function render(){
 if(!S){renderLogin();return;}
 const views={dashboard,businesses,search,detail,reviews,issues,metrics,feedback,settings:settingsView};
 shell((views[current]||dashboard)());
 // Dynamic bar lengths are set as CSS properties, never HTML from user input.
 $$('[data-bar]').forEach(el=>el.style.width=`${Math.max(0,Math.min(100,Number(el.dataset.bar)))}%`);
 if(current==='search')performSearch();
}
function route(){
 const [view,id]=(location.hash.slice(1)||'dashboard').split('/');
 current=(navigation.some(n=>n[0]===view)||view==='detail')?view:'dashboard';
 if(view==='detail')selectedCase=id;
 if(S){render();window.scrollTo(0,0);}
}
function closeModal(){const node=$('#modal-root');node.innerHTML='';modalContext=null;pageData=null;document.body.style.overflow='';$('#app').inert=false;if(lastFocus?.isConnected)lastFocus.focus();}
function showModal(title,content,context={},wide=false){
 lastFocus=document.activeElement;modalContext=context;
 $('#modal-root').innerHTML=`<div class="modal-backdrop"><section class="modal ${wide?'reader':''}" role="dialog" aria-modal="true" aria-labelledby="modal-title" tabindex="-1"><header class="modal-head"><h2 id="modal-title">${title}</h2><button class="icon-btn" data-action="close" aria-label="关闭对话框">${icon('close')}</button></header>${content}</section></div>`;
 document.body.style.overflow='hidden';$('#app').inert=true;setTimeout(()=>$('.modal input:not([type="hidden"]), .modal select, .modal textarea, .modal button')?.focus(),30);
}
function formModal(title,body,action,context={},submitLabel='确认保存'){
 showModal(title,`<form class="modal-body" data-form="${action}">${body}<div class="error-inline" id="form-error"></div><div class="modal-footer">${btn('取消','close')}<button class="btn primary" type="submit">${submitLabel}</button></div></form>`,{...context,action});
}
async function openReader(id,page=6){
 showModal('原卷核对', '<div class="loading">正在读取授权卷页…</div>',{action:'reader',id,page},true);
 try{const r=await api(`documents/${encodeURIComponent(id)}?page=${page}`);if(modalContext?.id!==id)return;pageData=r;drawReader();}
 catch(e){if(modalContext?.id===id){$('.modal').innerHTML=`<header class="modal-head"><h2>卷页暂不可用</h2>${btn('关闭','close')}</header>${empty('无法打开卷页',e.message,'lock')}`;}}
}
function drawReader(){
 const r=pageData,d=r.document;
 const eligible=S.businesses.filter(b=>owned(b)&&b.status==='定位中');
 const allowed=hasRole('organizer')&&d.status==='有效'&&S.settings.paused!=='true'&&eligible.length>0;
 const matched=selectedCase&&eligible.some(b=>b.id===selectedCase)?selectedCase:eligible[0]?.id;
 $('.modal').innerHTML=`<header class="modal-head"><h2>原卷核对 <span class="demo-label">合成卷页 · 非真实扫描件</span></h2><button class="icon-btn" data-action="close" aria-label="关闭对话框">${icon('close')}</button></header><div class="reader-body"><aside class="reader-nav"><div class="eyebrow">ARCHIVE READER</div><h3>${h(d.title)}</h3><p>${h(d.id)}<br>${h(d.version)} · ${d.pages} 页<br>${h(d.category)} · ${d.year} 年</p>${[Math.max(1,r.scan_page-1),r.scan_page,Math.min(d.pages,r.scan_page+1)].filter((p,i,a)=>a.indexOf(p)===i).map(p=>`<button class="thumb ${p===r.scan_page?'active':''}" data-action="reader-page" data-page="${p}" aria-label="扫描第 ${p} 页"><div class="thumb-lines"></div><div class="thumb-lines"></div><div class="thumb-lines"></div><div class="thumb-lines"></div><div class="thumb-label">第 ${p} 页</div></button>`).join('')}</aside><div class="reader-center"><form class="reader-toolbar" data-form="reader-page"><span>扫描页序号 <input class="input" type="number" name="page" min="1" max="${d.pages}" value="${r.scan_page}" aria-label="跳转扫描页"> / ${d.pages} <button class="btn small" type="submit">跳转</button></span><span>纸质页码 ${r.paper_page??'待核对'}</span></form><article class="paper"><div class="paper-watermark">合成演示资料</div><small>云 州 档 案 服 务 中 心 · 合 成 卷 页</small><h2>${h(d.title)}</h2><hr class="paper-rule"><small>${h(d.id)}　${h(d.version)}</small>${r.paragraphs.map(p=>`<p>${h(p)}</p>`).join('')}<div class="paper-page">— ${r.paper_page??'页码待核对'} —</div></article></div><aside class="reader-aside"><h3>引用依据核对</h3>${badge(d.status)}<dl><dt>档号 / 资料版本</dt><dd>${h(d.id)}<br>${h(d.version)}</dd><dt>扫描页 → 纸质页</dt><dd>第 ${r.scan_page} 页 → ${r.paper_page===null?'映射待修复':`第 ${r.paper_page} 页`}</dd><dt>资料来源</dt><dd>本地生成的合成卷页<br>不代表真实 OCR 或原始档案</dd></dl>${d.correction?`<div class="notice">${icon('swap')}<span>后续更正：${h(d.correction)}<br>${btn('查看更正文件','read',`data-id="${h(d.correction)}" data-page="1"`,'small mt')}</span></div>`:''}${d.status!=='有效'?`<div class="notice red">${icon('alert')}<span>此资料不能直接加入推荐依据，请转人工核对。</span></div>`:''}<form data-form="cite"><div class="field mt"><label for="reader-business">关联查阅业务</label><select id="reader-business" name="business_id" ${!allowed?'disabled':''}>${eligible.length?eligible.map(b=>`<option value="${h(b.id)}" ${b.id===matched?'selected':''}>${h(b.title)}</option>`).join(''):'<option>暂无由您负责的定位中业务</option>'}</select></div><label class="checkbox"><input type="checkbox" name="confirmed" required ${!allowed?'disabled':''}>我已核对本页内容、资料版本与纸质页码</label>${d.correction?`<label class="checkbox"><input type="checkbox" name="correction_checked" required ${!allowed?'disabled':''}>我已阅读后续更正，确认此引用的适用范围</label>`:''}<div class="error-inline" id="form-error"></div><button class="btn primary full" type="submit" ${!allowed?'disabled':''}>${icon('check')} 确认并加入业务依据</button></form><p class="muted">${!hasRole('organizer')?'卷页确认由档案整理员操作。':!eligible.length?'请先完成业务权限核验，再进行引用确认。':'引用将绑定资料版本，保存人工核对记录。'}</p>${hasRole('organizer','reviewer','engineer','protector')?btn('暂停建议 · 转人工','reader-issue','','full danger','alert'):''}</aside></div>`;
}
async function handleAction(action,el){
 const id=el.dataset.id;
 if(action==='close'){closeModal();return;}
 if(action==='refresh'){await refresh();render();toast('业务数据已刷新');return;}
 if(action==='task-tab'){taskTab=el.dataset.tab;render();return;}
 if(action==='issue-tab'){issueFilter=el.dataset.tab;render();return;}
 if(action==='create'){
  formModal('新建查阅申请',`<div class="notice sage">${icon('file')}<span>创建后进入权限核验，首期仅支持公开档案试点。此环境中的新业务仍属于演示数据。</span></div>${field('查阅事项','title','例如：县城总体规划批复查阅')}${field('申请单位','applicant','填写申请单位')}${textarea('查阅用途与范围','purpose','说明查阅目的、时期与希望核验的事项')}`,'create',{},'创建并提交权限核验');return;
 }
 if(action==='role'){
  showModal('切换演示岗位',`<form class="modal-body" data-form="role"><p class="list-note">每个岗位使用独立服务端会话，切换后按对应权限显示内容。演示账号使用同一本地演示口令。</p><div class="role-grid">${S.roles.map(r=>`<label class="role-option"><input type="radio" name="role" value="${r.id}" ${r.id===S.user.role?'checked':''}>${h(r.label)}<small>${h(r.name)}</small></label>`).join('')}</div>${field('演示口令','password','输入演示口令','ArcSync2026!','password')}<div class="error-inline" id="form-error"></div><div class="modal-footer">${btn('退出登录','logout','','','logout')}<button class="btn primary" type="submit">确认切换岗位</button></div></form>`,{action:'role'});return;
 }
 if(action==='logout'){await api('logout',{});S=null;closeModal();renderLogin();return;}
 if(action==='read'){await openReader(id,Number(el.dataset.page)||6);return;}
 if(action==='reader-page'){await openReader(modalContext.id,Number(el.dataset.page));return;}
 if(action==='help'){
  showModal('如何完成一次卷页核对',`<div class="modal-body"><ol class="rule-list">${['用标题、档号或关键词检索，可按类别、年度、资料状态筛选。','打开候选原卷，区分扫描页序号与纸质页码。当前卷页为合成演示资料。','如有后续更正，先核对更正文件与适用范围；已更正或待修复资料不能直接加入依据。','由整理员选择“定位中”的业务，完成人工核对后加入引用。','在业务详情提交审核，再由审核员确认接手并审核。','缺少依据、版本冲突或页码异常时暂停建议，转交人工处理。'].map((s,i)=>`<li><span class="rule-number">${i+1}</span><span>${s}</span></li>`).join('')}</ol><div class="modal-footer">${btn('开始检索','close','','primary')}</div></div>`);return;
 }
 if(action==='accept'){await api('actions/accept',{id:Number(id)});await refresh();render();toast('已确认接手，责任岗位已更新');return;}
 if(action==='sync'){await api('actions/sync',{});await refresh();render();toast('本地合成快照已刷新，未同步外部系统');return;}
 if(action==='pause'||action==='unpause'){
  formModal(action==='pause'?'暂停试点':'复盘后恢复试点',`<div class="notice ${action==='pause'?'red':'sage'}">${icon('shield')}<span>${action==='pause'?'暂停后停止正常业务推进，仍可查看资料、保存草稿和处理人工异常。':'恢复前请确认风险已经排除，并填写复盘依据。恢复仅对保护专员开放。'}</span></div>${textarea(action==='pause'?'暂停原因 / 触发条件':'复盘与恢复依据','reason','填写具体原因，记录将进入审计')}`,'control',{paused:action==='pause'},action==='pause'?'确认暂停':'确认恢复');return;
 }
 if(action==='feedback'){
  formModal('登记服务反馈',`${select('关联原业务','business_id',S.businesses.map(b=>[b.id,b.title+' · '+b.id]))}${field('当时使用的资料版本','version','例如：YZ-CJ-1986-001 / v2.0')}${textarea('反馈内容','content','描述需要核对的事项及实际交接情况')}`,'feedback',{},'登记并交审核核对');return;
 }
 if(action==='reply'){
  formModal('填写反馈核对结果',textarea('核对依据与结果','reply','写明核对的原业务、资料版本与交接情况，不作未经确认的承诺'),'reply',{id:Number(id)});return;
 }
 if(action==='resolve'){
  const issue=S.issues.find(i=>i.id===Number(id));
  formModal('填写异常处理结果',`<p class="list-note">${h(issue.kind)}：${h(issue.detail)}</p><div class="notice sage">${icon('shield')}<span>本次仅记录处理结果，不修改原档案或自动解除资料质量标记。处理后交审核员复核。</span></div>${textarea('处理依据与结果','resolution','填写核对过程、发现和后续建议')}`,'resolve',{id:Number(id)},'保存并提交复核');return;
 }
 if(action==='assign'){
  const issue=S.issues.find(i=>i.id===Number(id));
  formModal('分派异常处理人',`${select('实际处理岗位','receiver',S.roles.filter(r=>['organizer','reviewer','engineer','protector'].includes(r.id)).map(r=>[r.id,r.label+' · '+r.name]),issue.owner)}${textarea('分派说明 / 待解决事项','note','说明需要核对的问题与依据')}`,'assign',{id:Number(id),business_id:issue.business_id});return;
 }
 if(action==='reader-issue'){
  const docid=pageData?.document.id;
  const active=S.businesses.filter(b=>b.status!=='已交付');
  if(!active.length){toast('请先创建一笔查阅业务',true);return;}
  formModal('暂停建议并转人工',`${select('关联查阅业务','business_id',active.map(b=>[b.id,b.title]),selectedCase||active[0].id)}${issueFields()}`,'issue',{document_id:docid},'记录异常并转人工');return;
 }
 const b=id?businessById(id):null;
 if(action==='export'){
  const content=`云州档案 · 合成演示交付记录\n业务：${b.id}\n事项：${b.title}\n申请单位：${b.applicant}\n状态：${b.status}\n更新时间：${date(b.updated,true)}\n\n已确认依据：\n${b.citations.map(c=>`${c.document_id} / ${c.version} / 扫描第 ${c.scan_page} 页 / 纸质第 ${c.paper_page} 页`).join('\n')}\n\n随单说明：${b.note||'无'}\n\n此文件为本地合成数据演示，不作为真实档案交付凭证。`;
  const url=URL.createObjectURL(new Blob(['\ufeff'+content],{type:'text/plain;charset=utf-8'}));
  const a=document.createElement('a');a.href=url;a.download=b.id+'-交付记录.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('已导出合成业务交付记录');return;
 }
 if(!b)throw new Error('未找到关联业务，请刷新后重试');
 if(action==='authorize'){
  formModal('核验公开档案试点权限',`<p class="list-note">业务：${h(b.title)}<br>请核实申请用途与授权范围。当前为演示权限核验，不代表真实系统授权。</p>${select('本次核验范围','scope',[['公开档案试点','公开档案试点']])}<div class="notice sage">${icon('shield')}<span>不开放受限档案、不改变密级。核验通过后交整理员接手定位。</span></div>`,'authorize',{business_id:b.id},'确认权限并交接');return;
 }
 if(action==='issue'){formModal('暂停建议并转人工',issueFields(),'issue',{business_id:b.id,document_id:''},'记录异常并转人工');return;}
 if(action==='handoff'){
  const targets=S.roles.filter(r=>r.id!==S.user.role);
  formModal('发起责任交接',`<p class="list-note">业务：${h(b.id)} · 当前版本 r${b.revision}<br>交接包含当前状态、资料引用与未解决事项，对方确认接手才结束本岗待办。</p>${select('接手岗位','receiver',targets.map(r=>[r.id,r.label+' · '+r.name]))}${textarea('未解决事项与交接说明','note','说明本次处理结果和下一步需要核对的内容')}`,'handoff',{business_id:b.id},'发送并等待确认');return;
 }
 if(['submit','approve','return','deliver','resume'].includes(action)){
  const labels={submit:['提交审核','交接说明与待确认事项','提交并等待接手'],approve:['人工审核通过','审核依据与适用规则','确认审核通过'],return:['退回卷内定位','退回原因（必填）','确认退回'],deliver:['记录人工交付','交付对象、公开范围与交付说明','确认已人工交付'],resume:['复核后恢复定位','复核结论与恢复依据','恢复并交整理员接手']};
  const [title,label,submit]=labels[action];
  formModal(title,`<p class="list-note">业务：${h(b.title)}<br>已确认引用 ${b.citations.length} 条 · 当前状态 ${h(b.status)}</p>${textarea(label,action==='return'?'reason':'note','填写依据与处理说明，保留人工确认记录')}`,action,{business_id:b.id},submit);return;
 }
}
function issueFields(){return `${select('异常类型','kind',[['版本或更正关系异常','版本或更正关系异常'],['页码映射异常','页码映射异常'],['缺少引用依据','缺少引用依据'],['OCR 日期歧义','OCR 日期歧义'],['规则版本冲突','规则版本冲突'],['关键系统不可用','关键系统不可用']])}${textarea('触发原因与待核对内容','detail','写清涉及的资料、版本与具体问题，审核员将分派处理人')}`;}
async function submitForm(form){
 const kind=form.dataset.form, data=Object.fromEntries(new FormData(form));
 if(kind==='login'||kind==='role'){
  await api('login',data);await refresh();closeModal();route();toast(kind==='login'?'已进入档案工作台':'已切换岗位，按新岗位权限显示内容');return;
 }
 if(kind==='search'){
  searchQuery=data.q.trim();filters={category:data.category,year:data.year,status:data.status};await performSearch();return;
 }
 if(kind==='business-filter'){
  businessFilter=data.status;
  const q=data.q.trim().toLowerCase();const list=S.businesses.filter(b=>(!data.status||b.status===data.status)&&(!q||[b.title,b.id,b.applicant].some(v=>v.toLowerCase().includes(q))));
  $('#business-table').innerHTML=caseTable(list);$('#business-count').textContent=`${list.length} 笔`;return;
 }
 if(kind==='reader-page'){
  const id=modalContext.id;await openReader(id,Number(data.page));return;
 }
 let action=kind,payload={...modalContext,...data};delete payload.action;
 if(kind==='note')payload={business_id:form.dataset.id,note:data.note};
 if(kind==='control'){action='pause';payload.paused=modalContext.paused;}
 if(kind==='cite'){
  if(!pageData)throw new Error('请重新打开卷页');
  if(!data.confirmed)throw new Error('请先人工确认卷页内容');
  const d=pageData.document;
  payload={business_id:data.business_id,document_id:d.id,version:d.version,scan_page:pageData.scan_page,paper_page:pageData.paper_page,correction_checked:data.correction_checked==='on'};
 }
 if(payload.business_id){const b=businessById(payload.business_id);payload.revision=b?.revision;}
 const result=await api('actions/'+action,payload);
 await refresh();
 if(kind==='note'){render();toast('随单草稿已保存到本地数据库');return;}
 const bid=payload.business_id;
 closeModal();
 if(kind==='create'){location.hash='detail/'+result.id;}
 else {render();if(kind==='cite'&&bid)selectedCase=bid;}
 const messages={cite:'卷页引用已确认并加入业务依据',issue:'建议已暂停，异常已交审核员分派',handoff:'已发起交接，等待对方确认接手',submit:'已提交审核，等待审核员确认接手',authorize:'权限已核验，等待整理员确认接手',return:'已退回定位，等待整理员接手',resolve:'处理结果已记录，等待审核复核',pause:payload.paused?'试点已暂停':'试点已恢复'};
 toast(messages[action]||'已保存，处理记录已留痕');
}
document.addEventListener('click',async e=>{
 const el=e.target.closest('[data-action]');if(!el)return;
 if(busy)return;
 try{busy=true;await handleAction(el.dataset.action,el);}
 catch(err){toast(err.message,true);}
 finally{busy=false;}
});
document.addEventListener('submit',async e=>{
 const form=e.target.closest('form[data-form]');if(!form)return;e.preventDefault();
 if(busy)return;
 const submit=$('button[type="submit"]',form);if(submit)submit.disabled=true;busy=true;
 try{await submitForm(form);}
 catch(err){const error=$('#form-error',form)||$('.error-inline',form);if(error)error.textContent=err.message;else toast(err.message,true);if(err.message.includes('已更新')){await refresh();}}
 finally{busy=false;if(submit?.isConnected)submit.disabled=false;}
});
document.addEventListener('keydown',e=>{
 const modal=$('.modal');if(!modal)return;
 if(e.key==='Escape'){closeModal();return;}
 if(e.key==='Tab'){
  const nodes=$$('a[href],button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]',modal).filter(n=>n.offsetParent!==null);
  if(!nodes.length){e.preventDefault();modal.focus();return;}
  if(e.shiftKey&&document.activeElement===nodes[0]){e.preventDefault();nodes.at(-1).focus();}
  if(!e.shiftKey&&document.activeElement===nodes.at(-1)){e.preventDefault();nodes[0].focus();}
 }
});
window.addEventListener('hashchange',route);

(async()=>{try{await refresh();route();}catch{renderLogin();}})();
