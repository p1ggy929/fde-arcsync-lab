"""模型选择只允许运维配置的档案，不接受浏览器模型地址或密钥。"""
import json
import os
import re
from pathlib import Path
from ..core.service import Problem
from ..data_models.config import ModelSettings, Endpoint


def profiles():
    result={'current':('当前系统模型',ModelSettings.load())}
    path=os.getenv('ARCSYNC_EVALUATION_PROFILES')
    if not path:return result
    rows=json.loads(Path(path).read_text())
    for row in rows:
        key=row['id']
        if not re.fullmatch('[a-zA-Z0-9_-]{1,40}',key) or key=='current':raise Problem('评测模型档案标识无效',503)
        def endpoint(data):return Endpoint(data['provider'],data['base_url'].rstrip('/'),data['model'],os.getenv(data.get('api_key_env',''),'') if data.get('api_key_env') else '')
        result[key]=(row['label'],ModelSettings(endpoint(row['llm']),endpoint(row['embedding'])))
    return result


def select(key):
    row=profiles().get(key)
    if not row:raise Problem('评测模型不存在',400)
    row[1].validate()
    return row[1]


def metadata(config):
    return {'llm':{'provider':config.llm.provider,'model':config.llm.model,'fingerprint':config.llm.fingerprint},
            'embedding':{'provider':config.embedding.provider,'model':config.embedding.model,'fingerprint':config.embedding.fingerprint},
            'max_rounds':config.max_rounds,'max_tools':config.max_tools,'max_tokens':config.max_tokens,
            'restricted_context':config.allow_restricted}
