"""聊天使用SQLite短事务；模型生成期间不持有数据库连接。"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import uuid
from ..core.store import ROOT, Connection, now
from ..core.service import Problem
from ..data_models.config import database_kind

# 部分唯一索引在数据库层阻止同一会话的并发生成，多个进程同样生效。
SCHEMA='''
CREATE TABLE IF NOT EXISTS chat_sessions(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,title TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chat_messages(id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES chat_sessions(id),turn_no INTEGER NOT NULL,role TEXT NOT NULL CHECK(role IN ('user','assistant')),content TEXT NOT NULL DEFAULT '',status TEXT NOT NULL,citations_json TEXT NOT NULL DEFAULT '[]',sources_json TEXT NOT NULL DEFAULT '[]',error_code INTEGER NOT NULL DEFAULT 0,error_message TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,completed_at TEXT,lease_expires_at TEXT,UNIQUE(session_id,turn_no,role));
CREATE INDEX IF NOT EXISTS chat_session_owner ON chat_sessions(user_id,updated_at);
CREATE INDEX IF NOT EXISTS chat_message_turn ON chat_messages(session_id,turn_no);
CREATE UNIQUE INDEX IF NOT EXISTS chat_one_generating ON chat_messages(session_id) WHERE role='assistant' AND status='generating';
'''


def chat_connect():
    """SQLite模式复用原文件；MySQL业务模式另用SQLite保存聊天。"""
    default=os.getenv('ARCSYNC_DB',str(ROOT/'data'/'arcsync.sqlite3')) if database_kind()=='sqlite' else str(ROOT/'data'/'chat.sqlite3')
    path=os.getenv('ARCSYNC_CHAT_DB') or default
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=10,factory=Connection)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    # IF NOT EXISTS使旧库可增量升级，不删除或覆盖旧数据。
    db.executescript(SCHEMA)
    return db


def identity(user):
    if not user.get('user_id'):raise Problem('登录身份缺少用户标识，请重新登录',401)
    return user['user_id']


def owned(db,user,session_id):
    row=db.execute('SELECT * FROM chat_sessions WHERE id=? AND user_id=?',(session_id,identity(user))).fetchone()
    if not row:raise Problem('会话不存在',404)
    return dict(row)


def inspect_session(user,session_id):
    if session_id:
        with chat_connect() as db:return owned(db,user,session_id)


def expire_leases(db):
    # 进程异常退出后，租约到期的占位消息可以恢复；不误取消其他进程的活跃任务。
    db.execute("UPDATE chat_messages SET status='failed',content='',citations_json='[]',sources_json='[]',error_code=50302,error_message='上次生成已中断，请重新提问',completed_at=? WHERE status='generating' AND lease_expires_at<?",(now(),now()))


def start_turn(user,session_id,question,timeout_seconds=180):
    identifier=identity(user);t=now()
    with chat_connect() as db:
        db.execute('BEGIN IMMEDIATE');expire_leases(db)
        if session_id:owned(db,user,session_id)
        # 同一用户最多生成一条回答，避免多标签页同时调用模型。
        active=db.execute("SELECT 1 FROM chat_messages m JOIN chat_sessions s ON s.id=m.session_id WHERE s.user_id=? AND m.status='generating'",(identifier,)).fetchone()
        if active:raise Problem('当前会话或账号正在生成回答，请稍后再试',409)
        since=(datetime.now(timezone.utc)-timedelta(seconds=60)).isoformat()
        count=db.execute("SELECT COUNT(*) FROM chat_messages m JOIN chat_sessions s ON s.id=m.session_id WHERE s.user_id=? AND m.role='user' AND m.created_at>?",(identifier,since)).fetchone()[0]
        if count>=12:raise Problem('请求过于频繁，请稍后再试',429)
        if not session_id:
            session_id=uuid.uuid4().hex
            db.execute('INSERT INTO chat_sessions VALUES(?,?,?,?,?)',(session_id,identifier,question[:40],t,t))
        turn=db.execute('SELECT COALESCE(MAX(turn_no),0)+1 FROM chat_messages WHERE session_id=?',(session_id,)).fetchone()[0]
        question_id=uuid.uuid4().hex;answer_id=uuid.uuid4().hex
        db.execute('INSERT INTO chat_messages(id,session_id,turn_no,role,content,status,created_at,completed_at) VALUES(?,?,?,?,?,?,?,?)',
                   (question_id,session_id,turn,'user',question,'completed',t,t))
        expiry=(datetime.now(timezone.utc)+timedelta(seconds=timeout_seconds+30)).isoformat()
        db.execute('INSERT INTO chat_messages(id,session_id,turn_no,role,status,created_at,lease_expires_at) VALUES(?,?,?,?,?,?,?)',
                   (answer_id,session_id,turn,'assistant','generating',t,expiry))
        db.execute('UPDATE chat_sessions SET updated_at=? WHERE id=?',(t,session_id))
    return {'session_id':session_id,'message_id':answer_id,'turn_no':turn}


def check_turn(user,session_id,message_id):
    with chat_connect() as db:
        owned(db,user,session_id)
        row=db.execute('SELECT status,lease_expires_at FROM chat_messages WHERE id=? AND session_id=?',(message_id,session_id)).fetchone()
    if not row or row['status']!='generating' or row['lease_expires_at']<now():raise Problem('本次生成已结束或失效，请重新提问',409)


def finish(user,turn,status,answer='',sources=None,citations=None,code=0,message=''):
    with chat_connect() as db:
        db.execute('BEGIN IMMEDIATE');owned(db,user,turn['session_id'])
        cursor=db.execute("UPDATE chat_messages SET content=?,status=?,sources_json=?,citations_json=?,error_code=?,error_message=?,completed_at=? WHERE id=? AND session_id=? AND status='generating'",
                          (answer if status=='completed' else '',status,json.dumps(sources or [],ensure_ascii=False),json.dumps(citations or [],ensure_ascii=False),code,message,now(),turn['message_id'],turn['session_id']))
        if not cursor.rowcount and status=='completed':raise Problem('本次生成已失效，回答未保存',409)
        db.execute('UPDATE chat_sessions SET updated_at=? WHERE id=?',(now(),turn['session_id']))


def _validate_sources(user,sources):
    from ..data_models.knowledge import valid_sources
    from ..data_models.config import ModelSettings
    valid_sources(user,sources,ModelSettings.load().allow_restricted)


def history(user,session_id,before_turn,max_chars=12000):
    """最近6组完整问答；失效来源与失败轮次不作为下一轮证据。"""
    with chat_connect() as db:
        owned(db,user,session_id)
        rows=db.execute("SELECT q.content AS question,a.content AS answer,a.sources_json FROM chat_messages a JOIN chat_messages q ON q.session_id=a.session_id AND q.turn_no=a.turn_no AND q.role='user' WHERE a.session_id=? AND a.role='assistant' AND a.status='completed' AND q.status='completed' AND a.turn_no<? ORDER BY a.turn_no DESC LIMIT 6",(session_id,before_turn)).fetchall()
    pairs=[]
    for row in reversed(rows):
        try:_validate_sources(user,json.loads(row['sources_json']))
        except Problem:continue
        pairs.append([{'role':'user','content':row['question']},{'role':'assistant','content':row['answer']}])
    # 按整轮淘汰最早上下文，不能截断一半问答或加入当前未完成的问题。
    while pairs and sum(len(m['content']) for pair in pairs for m in pair)>max_chars:pairs.pop(0)
    return [message for pair in pairs for message in pair]


def sessions(user,limit=20,offset=0):
    with chat_connect() as db:
        rows=db.execute('SELECT id,title,created_at,updated_at FROM chat_sessions WHERE user_id=? ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?',(identity(user),limit+1,offset)).fetchall()
    more=len(rows)>limit
    return {'sessions':[dict(r) for r in rows[:limit]],'has_more':more,'next_offset':offset+limit if more else None}


def messages(user,session_id,limit=20,before_turn=None):
    with chat_connect() as db:
        db.execute('BEGIN IMMEDIATE');expire_leases(db);session=owned(db,user,session_id)
        turns=[r[0] for r in db.execute('SELECT DISTINCT turn_no FROM chat_messages WHERE session_id=? AND turn_no<? ORDER BY turn_no DESC LIMIT ?',
                                     (session_id,before_turn or 2**31,limit+1))]
        more=len(turns)>limit;visible=turns[:limit]
        rows=db.execute('SELECT * FROM chat_messages WHERE session_id=? AND turn_no>=? AND turn_no<=? ORDER BY turn_no,CASE role WHEN ? THEN 0 ELSE 1 END',
                        (session_id,min(visible),max(visible),'user')).fetchall() if visible else []
    items=[]
    for row in rows:
        item={k:row[k] for k in ('id','turn_no','role','content','status','error_code','error_message','created_at','completed_at')}
        item['citations']=json.loads(row['citations_json'])
        if row['role']=='assistant' and row['status']=='completed':
            try:_validate_sources(user,json.loads(row['sources_json']))
            except Problem:
                # 旧回答的权限/来源变化时不回传正文和片段，保留一个可理解的占位。
                item.update(content='',citations=[],status='unavailable',error_code=40902,error_message='依据或授权已变化，请重新提问')
        items.append(item)
    return {'session':session,'messages':items,'has_more':more,'next_before_turn':min(visible) if more else None}
