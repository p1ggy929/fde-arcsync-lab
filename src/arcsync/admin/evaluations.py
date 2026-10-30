"""云州档案评测题库；独立于检索知识库，避免预期答案污染业务回答。"""
import json
from collections import Counter
from ..core.store import ROOT

DATASET_PATH = ROOT / 'data' / 'evaluations' / 'yunzhou-archives.json'


def dataset():
    # 从随项目发布的JSON读取，不依赖模型配置，也不改写业务数据。
    items = json.loads(DATASET_PATH.read_text(encoding='utf-8'))
    return {
        'name': '云州档案项目评测',
        'total': len(items),
        'categories': dict(Counter(item['category'] for item in items)),
        'items': items,
    }
