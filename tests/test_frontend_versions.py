"""One UI generation for every role; reject mixed old scripts and new chat markup."""
from html.parser import HTMLParser
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from fastapi.testclient import TestClient
from arcsync.main import app
from arcsync.core.store import init_db, ROLES


class DocumentAssets(HTMLParser):
    def __init__(self,html):
        super().__init__();self.version=None;self.resources=[];self.scripts=[]
        self.feed(html)
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='meta' and attrs.get('name')=='arcsync-ui-version':self.version=attrs.get('content')
        if tag=='script' and attrs.get('src'):
            self.resources.append(attrs['src']);self.scripts.append(attrs['src'])
        if tag=='link' and attrs.get('rel')=='stylesheet' and attrs.get('href'):self.resources.append(attrs['href'])


class FrontendVersionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'ARCSYNC_ENV':'test','ARCSYNC_DATABASE_URL':'',
                                      'ARCSYNC_DB':str(Path(self.tmp.name)/'business.sqlite3'),
                                      'ARCSYNC_CHAT_DB':str(Path(self.tmp.name)/'chat.sqlite3')})
        self.env.start();init_db();self.client=TestClient(app)
    def tearDown(self):self.client.close();self.env.stop();self.tmp.cleanup()
    def login(self,role='organizer'):
        response=self.client.post('/api/login',json={'username':role,'password':os.getenv('ARCSYNC_DEMO_PASSWORD','ArcSync2026!')},headers={'X-ArcSync-Request':'1'})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()
    def version(self):
        response=self.client.get('/api/frontend/version')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.headers.get('cache-control'),'no-store')
        return response.json()['version']
    def test_all_six_roles_receive_same_shell_assets_and_chat_template(self):
        version=self.version();shells=[];panels=[]
        for role in ROLES:
            with self.subTest(role=role):
                user=self.login(role)
                shell=self.client.get(user['home']);self.assertEqual(shell.status_code,200,shell.text)
                shells.append(shell.text)
                assets=DocumentAssets(shell.text)
                self.assertEqual(assets.version,version)
                self.assertTrue(assets.resources)
                self.assertEqual(urlsplit(assets.scripts[0]).path,'/frontend-sync.js')
                for resource in assets.resources:
                    self.assertEqual(parse_qs(urlsplit(resource).query).get('v'),[version],resource)
                panel=self.client.get('/api/chat/view',params={'ui_version':version})
                self.assertEqual(panel.status_code,200,panel.text)
                self.assertEqual(panel.json()['version'],version)
                panels.append(panel.json()['html'])
                self.assertIn('configuration-label',panels[-1]);self.assertIn('toggle-sessions',panels[-1])
        self.assertEqual(len(set(shells)),1,'Employee/admin shells must load the exact same frontend generation')
        self.assertEqual(len(set(panels)),1,'Chat presentation must not vary by role')
    def test_shell_has_meta_and_all_resources_versioned_before_application_scripts(self):
        response=self.client.get('/');self.assertEqual(response.status_code,200)
        assets=DocumentAssets(response.text);version=self.version()
        self.assertEqual(assets.version,version)
        self.assertEqual(urlsplit(assets.scripts[0]).path,'/frontend-sync.js')
        self.assertTrue(any(urlsplit(url).path=='/app.js' for url in assets.resources))
        self.assertTrue(any(urlsplit(url).path=='/chat.js' for url in assets.resources))
        self.assertTrue(any(urlsplit(url).path=='/chat.css' for url in assets.resources))
        for resource in assets.resources:
            with self.subTest(resource=resource):
                self.assertEqual(parse_qs(urlsplit(resource).query).get('v'),[version])
                asset=self.client.get(resource)
                self.assertEqual(asset.status_code,200,resource)
                self.assertEqual(asset.headers.get('cache-control'),'no-store')
    def test_missing_or_stale_panel_version_is_rejected_before_mount(self):
        self.login()
        for params in (None,{'ui_version':'old-front-end'}):
            with self.subTest(params=params):
                response=self.client.get('/api/chat/view',params=params)
                self.assertEqual(response.status_code,409,response.text)
                self.assertNotIn('html',response.json())
        version=self.version();response=self.client.get('/api/chat/view',params={'ui_version':version})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['version'],version)
    def test_chat_panel_remains_authenticated_even_with_correct_version(self):
        response=self.client.get('/api/chat/view',params={'ui_version':self.version()})
        self.assertEqual(response.status_code,401,response.text)
    def test_stale_asset_generation_is_rejected_but_update_bootstrap_stays_available(self):
        for resource in ('app.js','chat.js','chat.css','style.css'):
            with self.subTest(resource=resource):
                response=self.client.get('/'+resource,params={'v':'old-front-end'})
                self.assertEqual(response.status_code,409,response.text[:200])
        response=self.client.get('/frontend-sync.js',params={'v':'old-front-end'})
        self.assertEqual(response.status_code,200,response.text[:200])
    def test_legacy_chat_entry_for_every_role_goes_to_same_workspace_view(self):
        for role in ROLES:
            with self.subTest(role=role):
                user=self.login(role)
                response=self.client.get('/chat-ui',follow_redirects=False)
                self.assertEqual(response.status_code,303)
                self.assertEqual(response.headers['location'],user['home']+'#chat')
    def test_css_changes_change_frontend_generation_without_backend_restart(self):
        from arcsync.core import frontend
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            frontend_dir=root/'frontend'
            frontend_dir.mkdir()
            (frontend_dir/'index.html').write_text('<html><head></head><body></body></html>')
            css=root/'chat.css'
            css.write_text('.archive-chat{display:grid}')
            (root/'chat.js').write_text('window.mountArchiveChat=()=>{};')
            (root/'chat-panel.html').write_text('<div class="archive-chat"></div>')
            with patch.object(frontend,'FRONTEND',frontend_dir):
                before=frontend.frontend_version()
                self.assertEqual(frontend.frontend_version(),before)
                css.write_text('.archive-chat{display:block}')
                after=frontend.frontend_version();self.assertNotEqual(after,before)
                css.write_text('.archive-chat{display:grid}')
                self.assertEqual(frontend.frontend_version(),before)


if __name__=='__main__':unittest.main()
