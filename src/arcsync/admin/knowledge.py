"""Parse, split and embed before atomically publishing a new knowledge document."""
import hashlib
from io import BytesIO
from pathlib import Path
import uuid
from datetime import datetime
from starlette.concurrency import run_in_threadpool
from ..core.store import connect, now, audit
from ..core.service import Problem
from ..data_models.config import ModelSettings, ModelError
from ..data_models.providers import ModelClient
from ..data_models.vectors import encoded_text


def parse(filename,raw):
    suffix=Path(filename).suffix.lower()
    if suffix=='.md':
        try:pages=[raw.decode('utf-8-sig')]
        except UnicodeDecodeError:raise Problem('Markdown 请使用 UTF-8 编码') from None
    elif suffix=='.pdf':
        from pypdf import PdfReader
        try:
            reader=PdfReader(BytesIO(raw))
            if reader.is_encrypted:raise ValueError()
            if len(reader.pages)>200:raise Problem('PDF 最多200页')
            pages=[p.extract_text() or '' for p in reader.pages]
        except Problem:raise
        except Exception:raise Problem('PDF 无法解析，请上传未加密的有效 PDF') from None
        if any(not p.strip() for p in pages):raise Problem('PDF 存在无文本页面，请先 OCR 后上传')
    else:raise Problem('仅支持 .md 或 .pdf')
    chunks=[]
    for page,content in enumerate(pages,1):
        for start in range(0,len(content),1000):
            text=content[max(0,start-150):start+1000].strip()
            if text:chunks.append((page,text))
    if not chunks:raise Problem('文件没有可用文本')
    if len(chunks)>500:raise Problem('文件切片超过500个，请拆分文件')
    return chunks


async def upload(user,filename,raw):
    if not filename or len(filename)>255 or '/' in filename or '\\' in filename:raise Problem('文件名无效')
    chunks=await run_in_threadpool(parse,filename,raw)
    config=ModelSettings.load();config.embedding.validate()
    embedding=ModelClient(config.embedding)
    identifier='KB-'+uuid.uuid4().hex;version='v1';title=Path(filename).stem[:200]
    records=[{'id':hashlib.sha256(f'{identifier}:{i}'.encode()).hexdigest(),'document_id':identifier,'version':version,'title':title,'content':content,'page':page,'scan_page':i+1} for i,(page,content) in enumerate(chunks)]
    vectors=[]
    for start in range(0,len(records),16):vectors.extend(await embedding.embed([encoded_text(c) for c in records[start:start+16]]))
    # Publication and vector persistence share one transaction: failures leave no visible document.
    def publish():
        import json
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(identifier,title,'制度知识库',datetime.now().year,'公开',version,len(records),'有效','',records[0]['content'][:800],1,now()))
            db.execute('INSERT INTO document_files VALUES(?,?,?)',(identifier,version,filename))
            for chunk,vector in zip(records,vectors):
                db.execute('INSERT INTO document_chunks VALUES(?,?,?,?,?,?,?)',(chunk['id'],identifier,version,chunk['scan_page'],chunk['page'],chunk['content'],'imported'))
                digest=hashlib.sha256(encoded_text(chunk).encode()).hexdigest();fingerprint=embedding.endpoint.fingerprint
                key=hashlib.sha256(f'{chunk["id"]}|{fingerprint}|{digest}'.encode()).hexdigest()
                db.execute('INSERT INTO vector_embeddings VALUES(?,?,?,?,?)',(key,chunk['id'],fingerprint,digest,json.dumps(vector)))
            audit(db,user,'上传制度知识库',detail={'id':identifier,'chunks':len(records),'filename':filename})
        return {'id':identifier,'chunks':len(records),'status':'已向量化入库'}
    return await run_in_threadpool(publish)


def documents():
    with connect() as db:return [dict(r) for r in db.execute("SELECT d.*,f.filename,(SELECT COUNT(*) FROM document_chunks c WHERE c.document_id=d.id) AS chunks FROM documents d JOIN document_files f ON f.document_id=d.id AND f.version=d.version WHERE d.category='制度知识库' ORDER BY d.updated DESC")]


def delete(user,identifier):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute("SELECT 1 FROM documents WHERE id=? AND category='制度知识库'",(identifier,)).fetchone():raise Problem('制度不存在',404)
        db.execute('DELETE FROM vector_embeddings WHERE chunk_id IN (SELECT id FROM document_chunks WHERE document_id=?)',(identifier,))
        db.execute('DELETE FROM document_chunks WHERE document_id=?',(identifier,))
        db.execute('DELETE FROM document_files WHERE document_id=?',(identifier,))
        db.execute('DELETE FROM documents WHERE id=?',(identifier,))
        audit(db,user,'删除制度知识库',detail={'id':identifier})
