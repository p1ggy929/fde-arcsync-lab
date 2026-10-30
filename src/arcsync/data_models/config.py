"""Environment-owned model routing; never accept provider URLs from web requests."""
from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / '.env', override=False)

class ModelError(Exception):
    """Safe, user-facing service error, excluding credentials and upstream bodies."""

@dataclass(frozen=True)
class Endpoint:
    provider: str
    base_url: str
    model: str
    api_key: str = ''

    @property
    def local(self):
        return urlsplit(self.base_url).hostname in ('localhost','127.0.0.1','::1')

    def validate(self):
        if self.provider not in ('cloud','ollama','vllm') or not self.model:
            raise ModelError('尚未配置模型，请在项目 .env 中设置模型提供方、地址和模型名。')
        url = urlsplit(self.base_url)
        if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ModelError('模型地址无效，请使用不含账号、密钥和查询参数的 HTTP 服务地址。')
        if not self.local and url.scheme != 'https':
            raise ModelError('远程模型地址请使用 HTTPS；本机模型可使用 HTTP。')
        if self.provider == 'cloud' and not self.api_key:
            raise ModelError('云端模型尚未配置 API 密钥。')

    @property
    def fingerprint(self):
        import hashlib
        return hashlib.sha256(f'{self.provider}|{self.base_url}|{self.model}'.encode()).hexdigest()

@dataclass(frozen=True)
class ModelSettings:
    llm: Endpoint
    embedding: Endpoint
    max_rounds: int = 10
    max_tools: int = 20
    max_tokens: int = 1600
    restricted_context: bool = False

    @classmethod
    def load(cls):
        provider=os.getenv('ARCSYNC_LLM_PROVIDER','disabled')
        base=os.getenv('ARCSYNC_LLM_BASE_URL','http://127.0.0.1:11434' if provider=='ollama' else 'http://127.0.0.1:8000/v1').rstrip('/')
        llm=Endpoint(provider,base,os.getenv('ARCSYNC_LLM_MODEL',''),os.getenv('ARCSYNC_LLM_API_KEY',''))
        embedding=Endpoint(os.getenv('ARCSYNC_EMBEDDING_PROVIDER',provider),os.getenv('ARCSYNC_EMBEDDING_BASE_URL',base).rstrip('/'),
                           os.getenv('ARCSYNC_EMBEDDING_MODEL',''),os.getenv('ARCSYNC_EMBEDDING_API_KEY',llm.api_key))
        return cls(llm,embedding,restricted_context=os.getenv('ARCSYNC_ALLOW_RESTRICTED_MODEL_CONTEXT','false').lower()=='true')

    def validate(self):
        self.llm.validate();self.embedding.validate()

    @property
    def allow_restricted(self):
        return self.restricted_context and self.llm.local and self.embedding.local and self.llm.provider!='cloud' and self.embedding.provider!='cloud'

    def public(self):
        try:
            self.validate();configured=True;message='配置已填写，连接状态需检查'
        except ModelError as error:
            configured=False;message=str(error)
        return {'configured':configured,'message':message,'provider':self.llm.provider,'model':self.llm.model,
                'embedding_provider':self.embedding.provider,'embedding_model':self.embedding.model,
                'restricted_context':self.allow_restricted,'vector_store':'FAISS','database':database_kind(),
                'max_rounds':self.max_rounds,'max_tools':self.max_tools}

def database_kind():
    return 'mysql' if os.getenv('ARCSYNC_DATABASE_URL','').startswith('mysql') else 'sqlite'
