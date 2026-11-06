"""服务端渲染的演示界面，所有用户数据在写入 HTML 前转义。"""
from html import escape
from urllib.parse import quote

from fastapi.responses import HTMLResponse


def h(value):
    return escape(str(value), quote=True)


def request_url(identifier):
    return '/requests/' + quote(str(identifier), safe='')


def archive_url(identifier):
    return '/archives/' + quote(str(identifier), safe='')


def hidden(name, value):
    return f'<input type="hidden" name="{h(name)}" value="{h(value)}">'


def layout(title, body, user=None, active='', status=200):
    navigation = ''
    if user:
        links = []
        for path, label, section in (('/', '档案检索', 'catalog'), ('/requests', '查阅申请', 'requests')):
            current = ' aria-current="page"' if active == section else ''
            links.append(f'<a href="{path}"{current}>{label}</a>')
        navigation = f'''
        <nav aria-label="工作台">{''.join(links)}</nav>
        <div class="account">
          <span>{h(user['name'])} / {h(user['label'])}</span>
          <a href="/login">切换岗位</a>
          <form action="/logout" method="post"><button class="quiet">退出</button></form>
        </div>'''
    return HTMLResponse(f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="云州档案公开档案查阅流程演示">
  <title>{h(title)} | 云州档案 MVP</title>
  <link rel="icon" href="/favicon.svg">
  <link rel="stylesheet" href="/mvp.css">
</head>
<body>
  <a class="skip" href="#main">跳到主要内容</a>
  <header class="top"><div class="wrap top-inner">
    <a class="brand" href="/">
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">
        <path d="M3 4h18v5H3z M5 9v11h14V9 M9 13h6"/>
      </svg>云州档案
    </a>
    {navigation}
  </div></header>
  <main id="main" class="wrap" tabindex="-1">{body}</main>
  <footer class="wrap footer">云州档案 MVP / 公开档案合成演示</footer>
</body>
</html>''', status_code=status)


def sign_in():
    return layout('登录', '''
    <section class="entry" aria-labelledby="heading">
      <div>
        <h1 id="heading">档案查阅工作台</h1>
        <p>用一份公开档案，走完申请、卷页核对与人工交付。</p>
        <ol class="roles">
          <li><strong>业务查询代表</strong> 提出查阅申请</li>
          <li><strong>查阅审核员</strong> 核验范围、审核并交付</li>
          <li><strong>档案整理员</strong> 核对卷页并提交审核</li>
        </ol>
      </div>
      <form action="/login" method="post" class="panel fields">
        <h2>登录演示岗位</h2>
        <label>岗位
          <select name="username" required>
            <option value="representative">叶书 / 业务查询代表</option>
            <option value="reviewer">唐映秋 / 查阅审核员</option>
            <option value="organizer">林亦舟 / 档案整理员</option>
          </select>
        </label>
        <label>密码 <input name="password" type="password" autocomplete="current-password" required maxlength="200"></label>
        <p class="muted">三个演示岗位的密码均为 <code>ArcSync2026!</code></p>
        <button class="primary">进入工作台</button>
      </form>
    </section>''')


def error(message, status):
    return layout('无法完成操作', f'''
    <section class="error" role="alert">
      <h1>无法完成操作</h1><p>{h(message)}</p>
      <a class="button" href="/">回工作台</a>
      <a class="button" href="/login">选择演示岗位</a>
    </section>''', status=status)


def catalog(user, q, documents):
    items = []
    for doc in documents:
        items.append(f'''
        <li class="record">
          <div>
            <p class="muted">{h(doc['id'])} / {h(doc['category'])} / {h(doc['year'])} 年</p>
            <h2><a href="{archive_url(doc['id'])}">{h(doc['title'])}</a></h2>
            <p>{h(doc['summary'])}</p>
          </div>
          <div class="record-side"><span class="badge">{h(doc['status'])}</span><span>{h(doc['version'])}</span></div>
        </li>''')
    results = f'<ul class="records">{"".join(items)}</ul>' if items else '''
      <div class="empty"><p>没有找到匹配的档案。</p><a href="/">清除关键词，查看全部档案</a></div>'''
    return layout('档案检索', f'''
      <h1>档案检索</h1>
      <p class="muted">按档号、标题或关键词查找公开资料。</p>
      <form action="/" class="toolbar">
        <label>关键词 <input name="q" value="{h(q)}" maxlength="200" type="search" placeholder="例如：总体规划"></label>
        <button class="primary">检索档案</button>
      </form>
      <p class="result-count">找到 {len(items)} 份档案</p>
      {results}''', user, 'catalog')


def archive(user, record, candidates):
    doc = record['document']
    current = record['scan_page']
    previous = f'<a class="button" href="?page={current - 1}">上一页</a>' if current > 1 else ''
    following = f'<a class="button" href="?page={current + 1}">下一页</a>' if current < doc['pages'] else ''
    paper = f"纸质第 {record['paper_page']} 页" if record['paper_page'] is not None else '纸质页码待核对'
    correction = ''
    if doc['correction']:
        correction = f'<p>版本关系请参阅 <a href="{archive_url(doc["correction"])}">档案 {h(doc["correction"])}</a>。</p>'
    paragraphs = ''.join(f'<p>{h(p)}</p>' for p in record['paragraphs'])
    citation = ''
    if user['role'] == 'organizer':
        if doc['status'] != '有效' or record['paper_page'] is None:
            citation = '<p class="empty">该资料已更正或待修复，请选择有效且页码明确的档案作为依据。</p>'
        elif not candidates:
            citation = '<p class="empty">暂无可关联的申请。请先在<a href="/requests">查阅申请</a>中接手已核验的申请。</p>'
        else:
            options = ''.join(
                f'<option value="{h(item["id"])}:{item["revision"]}">{h(item["title"])} / {h(item["id"])}</option>'
                for item in candidates
            )
            check = '<label class="check"><input type="checkbox" name="correction_checked" value="yes" required> 已核对后续更正关系</label>' if correction else ''
            citation = f'''
            <form class="panel fields" action="/actions/cite" method="post">
              <h2>加入申请依据</h2>
              <label>查阅申请 <select name="selection" required>{options}</select></label>
              {hidden('document_id', doc['id'])}{hidden('version', doc['version'])}
              {hidden('scan_page', current)}{hidden('paper_page', record['paper_page'])}
              <label class="check"><input type="checkbox" name="confirmed" value="yes" required> 已人工核对当前卷页</label>
              {check}
              <button class="primary">确认并加入依据</button>
            </form>'''
    return layout(doc['title'], f'''
      <a class="back" href="/">返回档案检索</a>
      <h1>{h(doc['title'])}</h1>
      <p class="muted">{h(doc['id'])} / {h(doc['version'])} / {h(doc['status'])}</p>
      {correction}
      <section class="panel folio" aria-labelledby="folio-title">
        <div class="folio-top"><h2 id="folio-title">扫描第 {current} 页 / {paper}</h2><span class="badge">合成卷页</span></div>
        {paragraphs}
        <nav class="pagination" aria-label="卷页导航">{previous}{following}</nav>
      </section>
      {citation}''', user, 'catalog')


def requests(user, items):
    create = ''
    if user['role'] == 'representative':
        create = '''
        <form class="panel fields" action="/actions/create" method="post">
          <h2>新建查阅申请</h2>
          <label>申请标题 <input name="title" required maxlength="100"></label>
          <label>申请单位 <input name="applicant" required maxlength="100"></label>
          <label>查阅用途 <textarea name="purpose" required maxlength="500" rows="3"></textarea></label>
          <button class="primary">提交申请</button>
        </form>'''
    rows = ''.join(f'''
      <li class="record">
        <div><p class="muted">{h(item['id'])}</p><h2><a href="{request_url(item['id'])}">{h(item['title'])}</a></h2></div>
        <div class="record-side"><span class="badge">{h(item['status'])}</span></div>
      </li>''' for item in items)
    listing = f'<ul class="records">{rows}</ul>' if rows else '<p class="empty">当前岗位没有可查阅的申请。</p>'
    return layout('查阅申请', f'''
      <h1>查阅申请</h1>
      <p class="muted">查看当前岗位可处理的申请和进度。</p>
      {create}
      <section aria-labelledby="request-list"><h2 id="request-list">申请记录</h2>{listing}</section>''', user, 'requests')


def action_form(item, action, label, controls=''):
    return f'''
      <form class="fields action" method="post" action="/actions/{action}">
        {hidden('business_id', item['id'])}{hidden('revision', item['revision'])}
        {controls}<button class="primary">{label}</button>
      </form>'''


def request_actions(user, item, pending):
    role = user['role']
    status = item['status']
    if pending:
        if pending['receiver'] == role:
            return action_form(item, 'accept', '确认接手', hidden('id', pending['id']))
        return '<p>交接已发起，等待下一岗位确认接手。</p>'
    if item['owner'] != role:
        return '<p>当前由其他岗位处理，可在下方查看流转记录。</p>'
    if role == 'reviewer' and status == '待权限核验':
        return action_form(item, 'authorize', '核验通过并交给整理员',
                           '<label class="check"><input type="checkbox" name="scope" value="公开档案试点" required> 已核验为公开档案试点</label>')
    if role == 'organizer' and status == '定位中':
        search = '<p><a class="button" href="/">前往检索并核对卷页</a></p>'
        if not item['citations']:
            return search + '<p>加入至少一条已核对的卷页依据后可提交审核。</p>'
        return search + action_form(item, 'submit', '提交审核',
                                    '<label>交接说明 <textarea name="note" required maxlength="2000" rows="2"></textarea></label>')
    if role == 'reviewer' and status == '待审核':
        approve = action_form(item, 'approve', '审核通过',
                              '<label>审核意见 <textarea name="note" required maxlength="2000" rows="2"></textarea></label>')
        send_back = action_form(item, 'return', '退回整理',
                                '<label>退回原因 <textarea name="reason" required maxlength="2000" rows="2"></textarea></label>')
        return approve + f'<details><summary>需要补充依据</summary>{send_back}</details>'
    if role == 'reviewer' and status == '待交付':
        return action_form(item, 'deliver', '记录人工交付',
                           '<label>交付说明 <textarea name="note" required maxlength="2000" rows="2"></textarea></label>')
    if status == '已交付':
        return '<p>已记录人工交付。</p>'
    return '<p>当前没有可执行的操作。</p>'


def request_detail(user, item, pending, events):
    citations = ''.join(
        f'<li><a href="{archive_url(c["document_id"])}?page={c["scan_page"]}">{h(c["document_id"])}</a>'
        f' / {h(c["version"])} / 扫描第 {c["scan_page"]} 页 / 纸质第 {c["paper_page"]} 页</li>'
        for c in item['citations']
    )
    trail = ''.join(
        f'<li><strong>{h(event["action"])}</strong><p>{h(event["actor"])}</p><p>{h(event["detail"])}</p></li>'
        for event in events
    )
    return layout(item['title'], f'''
      <a class="back" href="/requests">返回申请记录</a>
      <h1>{h(item['title'])}</h1>
      <p class="muted">{h(item['id'])} <span class="badge">{h(item['status'])}</span></p>
      <dl class="request-meta"><dt>申请单位</dt><dd>{h(item['applicant'])}</dd><dt>查阅用途</dt><dd>{h(item['purpose'])}</dd></dl>
      <section class="panel" aria-labelledby="action-title"><h2 id="action-title">当前操作</h2>{request_actions(user, item, pending)}</section>
      <section class="panel" aria-labelledby="citation-title">
        <h2 id="citation-title">卷页依据</h2>{f'<ul class="citations">{citations}</ul>' if citations else '<p class="muted">尚未确认卷页依据。</p>'}
      </section>
      <section class="panel" aria-labelledby="trail-title">
        <h2 id="trail-title">流转记录</h2><ol class="trail">{trail}</ol>
      </section>''', user, 'requests')
