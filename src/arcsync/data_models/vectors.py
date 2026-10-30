"""FAISS cosine search over pre-authorized chunks, with persistent embedding cache."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import faiss
import numpy as np
from starlette.concurrency import run_in_threadpool
from ..core.store import ROOT, connect
from .knowledge import available_chunks
from .config import ModelError

_WRITE_LOCK=threading.Lock()

def encoded_text(chunk):
    return f'{chunk["title"]}\n{chunk["document_id"]}\n{chunk["content"]}'

def read_cache(chunks,fingerprint):
    cached={}
    with connect() as db:
        for chunk in chunks:
            digest=hashlib.sha256(encoded_text(chunk).encode()).hexdigest()
            key=hashlib.sha256(f'{chunk["id"]}|{fingerprint}|{digest}'.encode()).hexdigest()
            row=db.execute('SELECT vector_json FROM vector_embeddings WHERE id=?',(key,)).fetchone()
            if row:cached[chunk['id']]=json.loads(row['vector_json'])
    return cached

def save_cache(chunks,vectors,fingerprint):
    with connect() as db:
        for chunk,vector in zip(chunks,vectors):
            digest=hashlib.sha256(encoded_text(chunk).encode()).hexdigest()
            key=hashlib.sha256(f'{chunk["id"]}|{fingerprint}|{digest}'.encode()).hexdigest()
            db.execute('INSERT OR IGNORE INTO vector_embeddings VALUES(?,?,?,?,?)',(key,chunk['id'],fingerprint,digest,json.dumps(vector)))

def rank(chunks,vectors,query_vector,question,limit,fingerprint):
    matrix=np.array(vectors,dtype='float32');query=np.array([query_vector],dtype='float32')
    if matrix.ndim!=2 or query.shape[1]!=matrix.shape[1] or not np.isfinite(matrix).all() or not np.isfinite(query).all():
        raise ModelError('嵌入模型维度变化，请保持索引与查询模型一致。')
    faiss.normalize_L2(matrix);faiss.normalize_L2(query)
    index=faiss.IndexFlatIP(matrix.shape[1]);index.add(matrix)
    scores,positions=index.search(query,len(chunks))
    ranked=[]
    for cosine,position in zip(scores[0],positions[0]):
        c=dict(chunks[int(position)])
        # Reciprocal rank fusion keeps lexical and semantic ranks comparable.
        ranked.append((c,float(cosine)))
    semantic={c['id']:i+1 for i,(c,_) in enumerate(ranked)}
    terms=question.casefold().split()
    lexical=sorted(chunks,key=lambda c:sum(encoded_text(c).casefold().count(t) for t in terms),reverse=True)
    lexical_rank={c['id']:i+1 for i,c in enumerate(lexical) if any(t in encoded_text(c).casefold() for t in terms)}
    fused=[]
    for c,cosine in ranked:
        score=1/(60+semantic[c['id']])+(1/(60+lexical_rank[c['id']]) if c['id'] in lexical_rank else 0)
        c.update(score=round(score,6),similarity=round(cosine,4));fused.append(c)
    fused.sort(key=lambda c:c['score'],reverse=True)
    # Immutable snapshot artifact; never load an untrusted FAISS binary or pickle.
    scope=hashlib.sha256((fingerprint+json.dumps([(c['id'],hashlib.sha256(encoded_text(c).encode()).hexdigest()) for c in chunks])).encode()).hexdigest()
    directory=Path(os.getenv('ARCSYNC_VECTOR_DIR',str(ROOT/'data'/'vectors')))
    with _WRITE_LOCK:
        directory.mkdir(parents=True,exist_ok=True)
        target=directory/(scope+'.faiss')
        if not target.exists():
            fd,name=tempfile.mkstemp(dir=directory,suffix='.tmp');os.close(fd)
            try:
                faiss.write_index(index,name);os.replace(name,target)
            finally:
                if os.path.exists(name):os.unlink(name)
    return fused[:limit]

async def hybrid_search(user,question,embedding,allow_restricted=False,limit=5,category='',year=None):
    chunks=await run_in_threadpool(available_chunks,user,allow_restricted)
    chunks=[c for c in chunks if (not category or c['category']==category) and (not year or c['year']==year)]
    if not chunks:return []
    if len(chunks)>2000:raise ModelError('当前授权资料超过本地索引容量，请先按类别或年度缩小范围。')
    fingerprint=embedding.endpoint.fingerprint
    cached=await run_in_threadpool(read_cache,chunks,fingerprint)
    missing=[c for c in chunks if c['id'] not in cached]
    for start in range(0,len(missing),16):
        batch=missing[start:start+16]
        vectors=await embedding.embed([encoded_text(c) for c in batch])
        await run_in_threadpool(save_cache,batch,vectors,fingerprint)
        cached.update({c['id']:v for c,v in zip(batch,vectors)})
    query=(await embedding.embed([question]))[0]
    return await run_in_threadpool(rank,chunks,[cached[c['id']] for c in chunks],query,question,limit,fingerprint)
