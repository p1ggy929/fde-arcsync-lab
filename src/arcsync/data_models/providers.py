"""Real Ollama NDJSON and OpenAI-compatible cloud/vLLM HTTP adapters."""
import json
import math
import httpx
from .config import Endpoint, ModelError
from ..production.controls import governed, governed_stream

class ModelClient:
    def __init__(self, endpoint: Endpoint, transport=None):
        endpoint.validate()
        self.endpoint=endpoint
        self.transport=transport

    def client(self):
        headers={'Authorization':'Bearer '+self.endpoint.api_key} if self.endpoint.api_key else {}
        return httpx.AsyncClient(base_url=self.endpoint.base_url+'/',headers=headers,
                                 timeout=httpx.Timeout(90,connect=5),trust_env=False,transport=self.transport)

    def payload(self,messages,tools=None,json_mode=False,stream=False,max_tokens=1600):
        ollama=self.endpoint.provider=='ollama'
        payload={'model':self.endpoint.model,'messages':messages,'stream':stream}
        if ollama:
            payload['options']={'temperature':0,'num_predict':max_tokens}
            if json_mode:payload['format']='json'
        else:
            payload.update(temperature=0,max_tokens=max_tokens)
            if stream:payload['stream_options']={'include_usage':True}
            if json_mode:payload['response_format']={'type':'json_object'}
        if tools:
            payload['tools']=tools
        return payload

    @governed
    async def chat(self,messages,tools=None,json_mode=False):
        try:
            async with self.client() as client:
                r=await client.post('api/chat' if self.endpoint.provider=='ollama' else 'chat/completions',
                                    json=self.payload(self.wire_messages(messages),tools,json_mode))
                r.raise_for_status();body=r.json()
            if self.endpoint.provider=='ollama':
                message=body['message'];usage={'input_tokens':body.get('prompt_eval_count',0),'output_tokens':body.get('eval_count',0)}
            else:
                choice=body['choices'][0]
                if choice.get('finish_reason')=='length':raise ModelError('模型输出超出长度限制，请缩小问题范围。')
                message=choice['message'];raw=body.get('usage') or {}
                usage={'input_tokens':raw.get('prompt_tokens',0),'output_tokens':raw.get('completion_tokens',0)}
            normalized={'role':'assistant','content':message.get('content') or ''}
            calls=[]
            for index,call in enumerate(message.get('tool_calls') or []):
                fn=call['function'];args=fn['arguments']
                if isinstance(args,str):args=json.loads(args)
                if not isinstance(args,dict):raise ValueError('tool arguments')
                calls.append({'id':call.get('id',f'call_{index}'),'type':'function',
                              'function':{'name':fn['name'],'arguments':json.dumps(args,ensure_ascii=False)}})
            if calls:normalized['tool_calls']=calls
            return normalized,usage
        except ModelError:raise
        except (httpx.HTTPError,ValueError,KeyError,TypeError,IndexError):
            raise ModelError('模型调用失败：请检查服务、模型名称及工具调用支持；上游详情未公开。') from None

    def wire_messages(self,messages):
        if self.endpoint.provider!='ollama':return messages
        normalized=[]
        for msg in messages:
            msg=dict(msg)
            if 'tool_calls' in msg:
                msg['tool_calls']=[{'function':{'name':c['function']['name'],'arguments':json.loads(c['function']['arguments'])}} for c in msg['tool_calls']]
            msg.pop('tool_call_id',None)
            normalized.append(msg)
        return normalized

    @governed_stream
    async def stream(self,messages,max_tokens=1600):
        try:
            async with self.client() as client:
                async with client.stream('POST','api/chat' if self.endpoint.provider=='ollama' else 'chat/completions',
                                         json=self.payload(self.wire_messages(messages),stream=True,max_tokens=max_tokens)) as response:
                    response.raise_for_status();finished=False
                    async for line in response.aiter_lines():
                        if not line:continue
                        if self.endpoint.provider=='ollama':
                            item=json.loads(line)
                            if item.get('error'):raise ModelError('模型流式生成失败。')
                            token=(item.get('message') or {}).get('content','')
                            if token:yield {'text':token}
                            if item.get('done'):
                                if item.get('done_reason')=='length':raise ModelError('模型生成达到长度限制，本次回答未完成。')
                                yield {'usage':{'input_tokens':item.get('prompt_eval_count',0),'output_tokens':item.get('eval_count',0)}}
                                finished=True;break
                        else:
                            if not line.startswith('data:'):continue
                            raw=line[5:].strip()
                            if raw=='[DONE]':finished=True;break
                            item=json.loads(raw)
                            if item.get('error'):raise ModelError('模型流式生成失败。')
                            for choice in item.get('choices',[]):
                                token=(choice.get('delta') or {}).get('content','')
                                if token:yield {'text':token}
                                if choice.get('finish_reason')=='length':raise ModelError('模型生成达到长度限制，本次回答未完成。')
                                if choice.get('finish_reason')=='stop':finished=True
                            if item.get('usage'):
                                raw=item['usage'];yield {'usage':{'input_tokens':raw.get('prompt_tokens',0),'output_tokens':raw.get('completion_tokens',0)}}
                    if not finished:raise ModelError('模型生成连接中断，本次回答未完成。')
        except ModelError:raise
        except (httpx.HTTPError,ValueError,KeyError,TypeError):
            raise ModelError('模型流式连接失败，请检查模型服务后重试。') from None

    @governed
    async def embed(self,texts):
        if not texts:return []
        try:
            payload={'model':self.endpoint.model,'input':texts}
            async with self.client() as client:
                response=await client.post('api/embed' if self.endpoint.provider=='ollama' else 'embeddings',json=payload)
                response.raise_for_status();body=response.json()
            vectors=body['embeddings'] if self.endpoint.provider=='ollama' else [d['embedding'] for d in sorted(body['data'],key=lambda d:d['index'])]
            if len(vectors)!=len(texts) or not vectors or not vectors[0]:raise ValueError('embedding count')
            dim=len(vectors[0])
            if dim>8192:raise ValueError('embedding dimension')
            for vector in vectors:
                if len(vector)!=dim or any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in vector) or sum(v*v for v in vector)==0:
                    raise ValueError('embedding vector')
            return vectors
        except (httpx.HTTPError,ValueError,KeyError,TypeError,IndexError):
            raise ModelError('向量模型调用失败或返回无效向量，请检查嵌入模型配置。') from None
