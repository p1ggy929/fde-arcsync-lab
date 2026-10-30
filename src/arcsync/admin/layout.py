"""Shared SSR workspace shell using the portal's existing design tokens and classes."""
from html import escape

PATHS={'archive': 'M3 5h18v5H3z M5 10v11h14V10 M9 14h6 M8 2h8', 'grid': 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z', 'search': 'M21 21l-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0', 'file': 'M14 2H5v20h14V7z M14 2v6h5 M8 12h8 M8 16h6', 'check': 'M5 12l4 4L20 5', 'clock': 'M12 8v5l3 2 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0', 'shield': 'M12 2l8 3v6c0 5-4 9-8 11-4-2-8-6-8-11V5z M8 12l3 3 5-6', 'chart': 'M4 3v18h17 M8 16v-5 M13 16V7 M18 16v-9', 'alert': 'M12 3L2 21h20z M12 9v5 M12 17v1', 'arrow': 'M5 12h14 M14 7l5 5-5 5', 'chevron': 'M9 5l7 7-7 7', 'down': 'M6 9l6 6 6-6', 'plus': 'M12 5v14 M5 12h14', 'close': 'M5 5l14 14 M19 5L5 19', 'users': 'M16 21v-3a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v3 M12 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0 M17 3a4 4 0 0 1 0 8 M19 14a4 4 0 0 1 4 4v3', 'swap': 'M3 7h17l-4-4 M21 17H4l4 4 M20 7l-4 4 M4 17l4-4', 'settings': 'M4 7h16 M4 17h16 M8 4v6 M16 14v6', 'download': 'M12 3v12 M7 10l5 5 5-5 M4 16v5h16v-5', 'refresh': 'M20 7a9 9 0 1 0 1 9 M20 2v6h-6', 'chat': 'M21 16a2 2 0 0 1-2 2H8l-5 4V4a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z M7 7h10 M7 12h7', 'eye': 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12 M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0', 'book': 'M12 5v16 M12 5C8 2 4 3 2 4v16c4-2 7-1 10 1 3-2 6-3 10-1V4c-4-2-7-1-10 1', 'logout': 'M9 3H3v18h6 M9 12h13 M17 7l5 5-5 5', 'lock': 'M6 10V7a6 6 0 0 1 12 0v3 M4 10h16v12H4z M12 15v3', 'inbox': 'M4 3h16v18H4z M4 14h5l2 3h2l2-3h5 M8 7h8 M8 10h6', 'pause': 'M8 5v14 M16 5v14'}


def icon(name):return f'<svg class="icon" aria-hidden="true" viewBox="0 0 24 24"><path d="{PATHS.get(name,PATHS["file"])}"/></svg>'


def render_sidebar(user,active=''):
    from ..core.store import connect
    e=lambda value:escape(str(value or ''),quote=True)
    home=user['home'];admin=user.get('portal')=='admin'
    with connect() as db:
        counts={
            'dashboard':db.execute("SELECT COUNT(*) FROM businesses WHERE owner=? AND status!='已交付'",(user['role'],)).fetchone()[0],
            'issues':db.execute("SELECT COUNT(*) FROM issues WHERE status!='已处理'").fetchone()[0],
            'reviews':db.execute("SELECT COUNT(*) FROM handoffs WHERE receiver=? AND status='待接手'",(user['role'],)).fetchone()[0],
        }
    nav=[('/access-drafts','查阅申请草稿','file','drafts')]
    if admin:nav.append(('/admin/manage','制度与问答管理','settings','manage'))
    nav.append((home+'#chat','智能查阅','chat','chat'))
    nav.extend([(home+'#dashboard','工作台','grid','dashboard'),(home+'#businesses','查阅业务','file','businesses'),(home+'#search','档案检索','search','search')])
    if admin:nav.append((home+'#models','模型与知识','settings','models'))
    nav.extend([(home+'#reviews','审核与交接' if admin else '我的交接','swap','reviews'),(home+'#issues','异常与修复','alert','issues')])
    if admin:nav.append((home+'#metrics','试点评估','chart','metrics'))
    nav.append((home+'#feedback','服务反馈','chat','feedback'))
    if admin:nav.append((home+'#settings','管理与审计','settings','settings'))
    links=''.join((('<div class="nav-label">评估与管理</div>' if key=='metrics' else '')+f'<a href="{e(url)}" class="{"active" if key==active else ""}" title="{label}"'+(' aria-current="page"' if key==active else '')+f' data-nav="{key}">{icon(symbol)}<span>{label}</span>'+ (f'<span class="nav-count">{counts[key]}</span>' if counts.get(key) else '')+'</a>') for url,label,symbol,key in nav)
    portal='管理后台 · B 端' if admin else '员工门户 · C 端'
    return f'''<aside class="sidebar"><a class="brand" href="{e(home)}#dashboard" aria-label="云州档案工作台"><div class="brandmark">{icon('archive')}</div><div><strong>云州档案</strong><small>YUNZHOU ARCHIVES</small></div></a><div class="nav-label">{portal}</div><nav class="nav">{links}</nav><div class="sidebar-bottom"><div class="pilot-card"><strong>有边界的智能辅助</strong><p>每一条建议都关联原卷依据<br>每一次交接都有明确责任</p><span>公开档案 · 小范围试点 {icon('arrow')}</span></div><div class="sidebar-foot"><span class="dot"></span> 本地服务运行中 · v0.3.0</div></div></aside>'''


def workspace_page(user,title,content,active,subtitle='',eyebrow='',tabs=''):
    e=lambda value:escape(str(value or ''),quote=True)
    home=user['home'];admin=user.get('portal')=='admin'
    portal='管理后台 · B 端' if admin else '员工门户 · C 端'
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)} · 云州档案</title><link rel="icon" href="/favicon.svg"><link rel="stylesheet" href="/style.css"><link rel="stylesheet" href="/admin/manage.css"><script src="/admin/manage.js" defer></script></head><body><div class="shell ssr-shell">{render_sidebar(user,active)}<div class="workspace"><header class="topbar"><div class="crumb"><span>{portal}</span><span>／</span><strong>{'制度与问答管理' if active=='manage' else '查阅申请草稿'}</strong></div><div class="top-right"><span class="mode-tag">合成数据演示</span><div class="profile"><span class="avatar">{e(user['name'][-2:])}</span><div><strong>{e(user['name'])}</strong><small>{e(user.get('label','管理员' if admin else '员工'))}</small></div></div></div></header><main class="main manage-main"><div class="page-heading"><div><div class="eyebrow">{e(eyebrow)}</div><h1>{e(title)}</h1><p>{e(subtitle)}</p></div><a class="btn" href="{'/chat-ui' if active=='drafts' else e(home)}">{'前往智能查阅' if active=='drafts' else '返回业务工作台'} ↗</a></div>{tabs}<div id="notice" role="status" aria-live="polite"></div>{content}<footer class="footer"><span>© 2026 云州档案服务中心 · ARCSYNC</span><span>原卷须人工确认 · 不改变开放范围与档案处置</span></footer></main></div></div></body></html>'''
