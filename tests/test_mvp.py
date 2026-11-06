"""覆盖 MVP 表单与已有业务规则之间的三岗位闭环。"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from arcsync.core.store import connect
from mvp.app import COOKIE, create_app


class MvpFlowTests(unittest.TestCase):
    def test_three_roles_confirm_and_deliver(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            'ARCSYNC_ENV': 'development',
            'ARCSYNC_DB': str(Path(folder) / 'mvp.sqlite3'),
            'ARCSYNC_DATABASE_URL': '',
            'ARCSYNC_DEMO_PASSWORD': 'ArcSync2026!',
            'ARCSYNC_JWT_SECRET': '',
        }), TestClient(create_app(), base_url='http://localhost') as client:
            origin = {'Origin': 'http://localhost'}

            def post(path, **fields):
                return client.post(path, data=fields, headers=origin, follow_redirects=False)

            def sign_in(role):
                response = post('/login', username=role, password='ArcSync2026!')
                self.assertEqual(response.status_code, 303, response.text)
                self.assertIn(COOKIE, response.cookies)
                self.assertNotIn('arcsync_session', response.cookies)

            def progress(action, **fields):
                response = post('/actions/' + action, business_id=identifier, **fields)
                self.assertEqual(response.status_code, 303, response.text)
                self.assertEqual(response.headers['location'], detail)

            def handoff_id():
                with connect() as db:
                    return db.execute(
                        "SELECT id FROM handoffs WHERE business_id=? AND status='待接手'", (identifier,),
                    ).fetchone()['id']

            def revision():
                with connect() as db:
                    return db.execute('SELECT revision FROM businesses WHERE id=?', (identifier,)).fetchone()['revision']

            self.assertEqual(client.get('/', follow_redirects=False).headers['location'], '/login')
            self.assertEqual(client.post('/login', data={'username': 'reviewer', 'password': 'ArcSync2026!'}).status_code, 403)
            sign_in('representative')
            created = post('/actions/create', title='MVP 规划查阅', applicant='演示单位', purpose='核对调整范围')
            self.assertEqual(created.status_code, 303, created.text)
            detail = created.headers['location']
            identifier = detail.rsplit('/', 1)[1]
            self.assertIn('待权限核验', client.get(detail).text)
            self.assertEqual(client.get('/archives/YZ-R-1991-002').status_code, 404)
            refused = post('/actions/authorize', business_id=identifier, revision=revision(), scope='公开档案试点')
            self.assertEqual(refused.status_code, 403)

            sign_in('reviewer')
            progress('authorize', revision=revision(), scope='公开档案试点')
            sign_in('organizer')
            self.assertIn('确认接手', client.get(detail).text)
            progress('accept', id=handoff_id())
            self.assertIn('关于县城总体规划局部调整的通知', client.get('/', params={'q': '总体规划'}).text)
            archive = client.get('/archives/YZ-CJ-1988-003', params={'page': 1})
            self.assertIn('纸质第 25 页', archive.text)
            self.assertIn('确认并加入依据', archive.text)
            evidence = {
                'selection': f'{identifier}:{revision()}', 'document_id': 'YZ-CJ-1988-003',
                'version': 'v1.0', 'scan_page': 1, 'paper_page': 25,
            }
            self.assertEqual(post('/actions/cite', **evidence).status_code, 400)
            cited = post('/actions/cite', **evidence, confirmed='yes')
            self.assertEqual(cited.status_code, 303, cited.text)
            self.assertEqual(cited.headers['location'], detail)
            progress('submit', revision=revision(), note='已核对扫描页和纸质页')

            sign_in('reviewer')
            progress('accept', id=handoff_id())
            progress('approve', revision=revision(), note='引用与卷页一致')
            progress('deliver', revision=revision(), note='已人工交付')
            sign_in('representative')
            delivered = client.get(detail)
            self.assertIn('已交付', delivered.text)
            self.assertIn('YZ-CJ-1988-003', delivered.text)
            self.assertIn('记录人工交付', delivered.text)
            self.assertEqual(client.get('/tokens.css').status_code, 200)
