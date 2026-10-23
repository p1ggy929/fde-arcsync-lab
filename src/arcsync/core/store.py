"""SQLite persistence, synthetic fixtures and append-only application audit."""
import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ROLES = {
    'organizer': ('林亦舟', '档案整理员'), 'reviewer': ('唐映秋', '查阅审核员'),
    'protector': ('傅景', '档案保护专员'), 'engineer': ('秦芮', '档案系统工程师'),
    'director': ('顾明岚', '档案服务主任'), 'representative': ('叶书', '业务查询代表'),
}

def now():
    return datetime.now(timezone.utc).isoformat()

class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()

def connect(path=None):
    target = path or os.environ.get('ARCSYNC_DB', str(ROOT / 'data' / 'arcsync.sqlite3'))
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(target, timeout=15, factory=Connection)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db

def audit(db, user, action, business_id='', detail=None):
    db.execute('INSERT INTO audit(at,actor,role,action,business_id,detail) VALUES(?,?,?,?,?,?)',
               (now(), user['name'], user['role'], action, business_id, json.dumps(detail or {}, ensure_ascii=False)))

def event(db, bid, user, action, detail=''):
    db.execute('INSERT INTO events(business_id,at,actor,action,detail) VALUES(?,?,?,?,?)',
               (bid, now(), user['name'], action, detail))

def init_db(path=None):
    with connect(path) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users(role TEXT PRIMARY KEY,name TEXT,salt TEXT,password TEXT);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,role TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,title TEXT,category TEXT,year INTEGER,level TEXT,
          version TEXT,pages INTEGER,status TEXT,correction TEXT,summary TEXT,paper_start INTEGER,updated TEXT);
        CREATE TABLE IF NOT EXISTS businesses(id TEXT PRIMARY KEY,title TEXT,applicant TEXT,purpose TEXT,
          status TEXT,owner TEXT,created TEXT,updated TEXT,note TEXT DEFAULT '',revision INTEGER DEFAULT 1,
          citations TEXT DEFAULT '[]',return_reason TEXT DEFAULT '',sample INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS handoffs(id INTEGER PRIMARY KEY,business_id TEXT,sender TEXT,receiver TEXT,
          note TEXT,status TEXT,created TEXT,accepted TEXT);
        CREATE TABLE IF NOT EXISTS issues(id INTEGER PRIMARY KEY,business_id TEXT,document_id TEXT,
          kind TEXT,detail TEXT,owner TEXT,status TEXT,created TEXT,resolution TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,business_id TEXT,at TEXT,actor TEXT,action TEXT,detail TEXT);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,at TEXT,actor TEXT,role TEXT,action TEXT,business_id TEXT,detail TEXT);
        CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY,business_id TEXT,version TEXT,content TEXT,
          status TEXT,created TEXT,reply TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
        CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'audit is append only'); END;
        CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'audit is append only'); END;
        ''')
        for role, (name, _) in ROLES.items():
            salt = secrets.token_hex(16)
            password = hashlib.pbkdf2_hmac('sha256', os.environ.get('ARCSYNC_DEMO_PASSWORD', 'ArcSync2026!').encode(), salt.encode(), 120000).hex()
            db.execute('INSERT OR IGNORE INTO users VALUES(?,?,?,?)', (role,name,salt,password))
        if db.execute('SELECT COUNT(*) FROM documents').fetchone()[0]:
            return
        t = now()
        docs = [
          ('YZ-CJ-1986-001','云州县城总体规划批复及附图','城市建设',1986,'公开','v2.0',24,'有效','YZ-CJ-1988-003','关于县城功能分区、主干道路与公共设施的规划批复。重点依据位于第 6—8 页。',1),
          ('YZ-CJ-1985-017','云州县城总体规划报审材料','城市建设',1985,'公开','v1.0',32,'已更正','YZ-CJ-1986-001','县城总体规划早期报审稿。已由后续批复更正，不能直接作为当前依据。',1),
          ('YZ-CJ-1988-003','关于县城总体规划局部调整的通知','城市建设',1988,'公开','v1.0',12,'有效','','关于东片区用地与道路走向的局部调整，是 1986 年规划批复的后续更正。',25),
          ('YZ-CJ-1992-008','城东片区道路建设竣工验收记录','城市建设',1992,'公开','v1.1',18,'有效','','城东片区道路建设工程验收记录，含项目范围、验收意见与签章。',41),
          ('YZ-MS-1990-021','云州县公共服务设施建设汇编','民生服务',1990,'公开','v1.0',42,'有效','','学校、卫生院与文化站等公共服务设施建设情况汇编。',1),
          ('YZ-CJ-1987-009','县城规划用地登记表及说明','城市建设',1987,'公开','v1.0',16,'待修复','','扫描页码与纸质页码存在歧义，OCR 日期待人工校核。',13),
          ('YZ-NJ-1989-006','云州县统计年鉴·城市建设分册','统计年鉴',1989,'公开','v1.0',56,'有效','','年度城市建设统计资料与项目目录，可用于交叉核对。',1),
          ('YZ-R-1991-002','内部专项工作会议记录','内部资料',1991,'受限','v1.0',20,'有效','','仅限档案保护专员在授权范围内核验，不向其他岗位返回。',1),
        ]
        db.executemany('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',[(*d,t) for d in docs])
        fixtures = [
          ('CY-2026-1023-001','县城总体规划批复查阅','市规划研究室','规划沿革核验','定位中','organizer'),
          ('CY-2026-1023-002','城东道路竣工资料查阅','城市建设服务中心','项目资料复核','待审核','reviewer'),
          ('CY-2026-1023-003','规划用地登记信息核对','土地整理工作组','历史用地信息核对','异常处理中','engineer'),
          ('CY-2026-1023-004','公共服务设施建设资料','公共服务研究组','历史资料研究','待权限核验','reviewer'),
          ('CY-2026-1023-005','年度城市建设统计资料','地方志编研室','编研引用','已交付','representative'),
        ]
        for bid,title,app,purpose,status,owner in fixtures:
            db.execute('INSERT INTO businesses(id,title,applicant,purpose,status,owner,created,updated,sample) VALUES(?,?,?,?,?,?,?,?,1)',(bid,title,app,purpose,status,owner,t,t))
            event(db,bid,{'name':'演示数据'},'创建合成样例','仅用于流程演示，不计入试点指标')
        citation = [{'document_id':'YZ-CJ-1992-008','version':'v1.1','scan_page':6,'paper_page':46,'confirmed':True}]
        db.execute('UPDATE businesses SET citations=? WHERE id=?',(json.dumps(citation),'CY-2026-1023-002'))
        db.execute('INSERT INTO issues(business_id,document_id,kind,detail,owner,status,created) VALUES(?,?,?,?,?,?,?)',
            ('CY-2026-1023-003','YZ-CJ-1987-009','页码映射异常','扫描第 4 页与纸质页码对应关系不明确，OCR 日期存在歧义。','engineer','待处理',t))
        for k,v in {'paused':'false','sync_at':t,'sync_state':'healthy','rule_version':'试点规则 v0.1','evaluation_retention_days':'90'}.items():
            db.execute('INSERT INTO settings VALUES(?,?)',(k,v))
