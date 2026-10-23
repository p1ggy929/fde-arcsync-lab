"""Run against the local server: python3 examples/demo.py. Read-only after login."""
import json
from http.cookiejar import CookieJar
from urllib.request import build_opener, HTTPCookieProcessor, Request
from urllib.parse import quote
import os
client=build_opener(HTTPCookieProcessor(CookieJar()))
base='http://127.0.0.1:8765/api/'
login=Request(base+'login',data=json.dumps({'role':'organizer','password':os.environ.get('ARCSYNC_DEMO_PASSWORD','ArcSync2026!')}).encode(),headers={'Content-Type':'application/json','X-ArcSync-Request':'1'})
with client.open(login) as response: print('已登录：',json.load(response)['name'])
with client.open(base+'search?status='+quote('有效')) as response:
    docs=json.load(response)['documents']
    print(f'获得 {len(docs)} 份公开有效的合成档案')
    for d in docs: print(d['id'],d['title'],d['version'])
