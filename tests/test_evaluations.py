"""评测数据完整性、工具兼容性及管理权限回归。"""
import json
import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from arcsync.main import app
from arcsync.core.store import init_db
from arcsync.admin.evaluations import dataset
from arcsync.agents.tools import MODELS


class EvaluationTests(unittest.TestCase):
    def test_dataset_contract_and_tools(self):
        data=dataset()
        self.assertEqual(data['total'],30)
        self.assertEqual(Counter(x['category'] for x in data['items']),{
            '制度类':7,'制度类-陷阱题':3,'工具调用类':10,'违规意图类':5,'多轮上下文类':5})
        self.assertEqual(len({x['question'] for x in data['items']}),30)
        for item in data['items']:
            self.assertEqual(set(item),{'question','expected_tools','expected_behavior','category'})
            self.assertTrue(item['question'] and item['expected_behavior'])
            self.assertIsInstance(item['expected_tools'],list)
            self.assertTrue(set(item['expected_tools']) <= set(MODELS))
            if item['category']=='制度类-陷阱题':
                self.assertIn('未找到',item['expected_behavior'])

    def test_admin_page_api_download_and_access(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{
            'ARCSYNC_DB':str(Path(folder)/'business.sqlite3'),
            'ARCSYNC_CHAT_DB':str(Path(folder)/'chat.sqlite3'),
            'ARCSYNC_DATABASE_URL':''}), TestClient(app) as client:
            paths=('/api/admin/evaluations','/api/admin/evaluations/download',
                   '/admin/manage?section=evaluation')
            for path in paths:self.assertEqual(client.get(path).status_code,401)
            def login(role):
                response=client.post('/api/login',json={'username':role,
                    'password':os.getenv('ARCSYNC_DEMO_PASSWORD','ArcSync2026!')},
                    headers={'X-ArcSync-Request':'1'})
                self.assertEqual(response.status_code,200)
            login('organizer')
            for path in paths:self.assertEqual(client.get(path).status_code,403)
            login('director')
            response=client.get(paths[0])
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['code'],0)
            self.assertEqual(response.json()['data']['total'],30)
            download=client.get(paths[1])
            self.assertEqual(download.status_code,200)
            self.assertEqual(download.json(),dataset()['items'])
            self.assertIn('attachment',download.headers['content-disposition'])
            html=client.get(paths[2])
            self.assertEqual(html.status_code,200)
            self.assertIn('30 条',html.text)
            self.assertIn('期望工具顺序',html.text)
            self.assertIn('下载 JSON 题库',html.text)


if __name__=='__main__':unittest.main()
