"""MySQL 8 adapter for the existing parameterized business repository."""
import re
from urllib.parse import urlsplit, unquote
import pymysql

class Row(dict):
    def __getitem__(self,key):
        return list(self.values())[key] if isinstance(key,int) else super().__getitem__(key)

class Result:
    def __init__(self,cursor):
        self.rows=[Row(row) for row in cursor.fetchall()] if cursor.description else []
        self.lastrowid=cursor.lastrowid;self.rowcount=cursor.rowcount;self.offset=0
    def fetchone(self):
        if self.offset>=len(self.rows):return None
        row=self.rows[self.offset];self.offset+=1;return row
    def fetchall(self):
        rows=self.rows[self.offset:];self.offset=len(self.rows);return rows
    def __iter__(self):
        return iter(self.fetchall())

class MySQLConnection:
    dialect='mysql'
    def __init__(self,url):
        parsed=urlsplit(url)
        if parsed.scheme not in ('mysql','mysql+pymysql') or not parsed.hostname or not parsed.path.strip('/') or parsed.query:
            raise ValueError('ARCSYNC_DATABASE_URL 必须为 mysql://用户:密码@主机:端口/数据库；不支持查询参数')
        self.raw=pymysql.connect(host=parsed.hostname,port=parsed.port or 3306,user=unquote(parsed.username or ''),
                                 password=unquote(parsed.password or ''),database=unquote(parsed.path.lstrip('/')),
                                 charset='utf8mb4',cursorclass=pymysql.cursors.DictCursor,autocommit=False,
                                 connect_timeout=5,read_timeout=30,write_timeout=30)
        self.lock_name='arcsync:'+parsed.path.lstrip('/')[:40]+':write'
        self.locked=False
    def __enter__(self):return self
    def __exit__(self,kind,value,traceback):
        try:
            if kind:self.raw.rollback()
            else:self.raw.commit()
        finally:
            try:
                if self.locked:
                    with self.raw.cursor() as cursor:cursor.execute('SELECT RELEASE_LOCK(%s)',(self.lock_name,))
            finally:self.raw.close()
    def execute(self,sql,args=()):
        if sql=='BEGIN IMMEDIATE':
            with self.raw.cursor() as cursor:
                cursor.execute('SELECT GET_LOCK(%s,15)',(self.lock_name,))
                if next(iter(cursor.fetchone().values()))!=1:raise TimeoutError('业务写入繁忙，请重试')
                self.locked=True
            self.raw.begin()
            return None
        sql=sql.replace('INSERT OR IGNORE','INSERT IGNORE').replace('?', '%s')
        sql=re.sub(r'(?<![\w`])key(?![\w`])','`key`',sql)
        with self.raw.cursor() as cursor:
            cursor.execute(sql,args)
            return Result(cursor)
    def executemany(self,sql,args):
        for row in args:self.execute(sql,row)

# Explicit lengths and auto-increment keys, no SQLite schema emulation.
SCHEMA = [
"""CREATE TABLE IF NOT EXISTS users(role VARCHAR(32) PRIMARY KEY,name VARCHAR(100),salt VARCHAR(64),password VARCHAR(128))""",
"""CREATE TABLE IF NOT EXISTS sessions(token VARCHAR(128) PRIMARY KEY,role VARCHAR(32),created VARCHAR(40))""",
"""CREATE TABLE IF NOT EXISTS documents(id VARCHAR(120) PRIMARY KEY,title VARCHAR(255),category VARCHAR(100),year INT,level VARCHAR(32),version VARCHAR(64),pages INT,status VARCHAR(32),correction VARCHAR(120),summary TEXT,paper_start INT,updated VARCHAR(40))""",
"""CREATE TABLE IF NOT EXISTS businesses(id VARCHAR(120) PRIMARY KEY,title VARCHAR(255),applicant VARCHAR(255),purpose TEXT,status VARCHAR(32),owner VARCHAR(32),created VARCHAR(40),updated VARCHAR(40),note TEXT DEFAULT (''),revision INT DEFAULT 1,citations LONGTEXT DEFAULT ('[]'),return_reason TEXT DEFAULT (''),sample INT DEFAULT 0)""",
"""CREATE TABLE IF NOT EXISTS handoffs(id BIGINT PRIMARY KEY AUTO_INCREMENT,business_id VARCHAR(120),sender VARCHAR(32),receiver VARCHAR(32),note TEXT,status VARCHAR(32),created VARCHAR(40),accepted VARCHAR(40))""",
"""CREATE TABLE IF NOT EXISTS issues(id BIGINT PRIMARY KEY AUTO_INCREMENT,business_id VARCHAR(120),document_id VARCHAR(120),kind VARCHAR(255),detail TEXT,owner VARCHAR(32),status VARCHAR(32),created VARCHAR(40),resolution TEXT DEFAULT (''))""",
"""CREATE TABLE IF NOT EXISTS events(id BIGINT PRIMARY KEY AUTO_INCREMENT,business_id VARCHAR(120),at VARCHAR(40),actor VARCHAR(100),action VARCHAR(255),detail TEXT)""",
"""CREATE TABLE IF NOT EXISTS audit(id BIGINT PRIMARY KEY AUTO_INCREMENT,at VARCHAR(40),actor VARCHAR(100),role VARCHAR(32),action VARCHAR(255),business_id VARCHAR(120),detail TEXT)""",
"""CREATE TABLE IF NOT EXISTS feedback(id BIGINT PRIMARY KEY AUTO_INCREMENT,business_id VARCHAR(120),version VARCHAR(64),content TEXT,status VARCHAR(32),created VARCHAR(40),reply TEXT DEFAULT (''))""",
"""CREATE TABLE IF NOT EXISTS settings(`key` VARCHAR(100) PRIMARY KEY,value TEXT)""",
"""CREATE TABLE IF NOT EXISTS document_chunks(id VARCHAR(160) PRIMARY KEY,document_id VARCHAR(120),version VARCHAR(64),scan_page INT,paper_page INT,content TEXT,source VARCHAR(32))""",
"""CREATE TABLE IF NOT EXISTS vector_embeddings(id VARCHAR(64) PRIMARY KEY,chunk_id VARCHAR(160),fingerprint VARCHAR(64),content_hash VARCHAR(64),vector_json LONGTEXT)""",
"""CREATE TABLE IF NOT EXISTS agent_runs(id VARCHAR(64) PRIMARY KEY,role VARCHAR(32),question TEXT,status VARCHAR(32),answer LONGTEXT,sources LONGTEXT,created VARCHAR(40),updated VARCHAR(40),usage TEXT,error TEXT)""",
"""CREATE TABLE IF NOT EXISTS agent_steps(id BIGINT PRIMARY KEY AUTO_INCREMENT,run_id VARCHAR(64),at VARCHAR(40),kind VARCHAR(32),detail LONGTEXT)""",
]

def initialize(db):
    for statement in SCHEMA:db.execute(statement+' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin')
    # MySQL does not offer CREATE TRIGGER IF NOT EXISTS on every supported release.
    for event in ('UPDATE','DELETE'):
        name='audit_no_'+event.lower()
        exists=db.execute('SELECT 1 FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() AND TRIGGER_NAME=?',(name,)).fetchone()
        if not exists:
            db.execute(f"CREATE TRIGGER {name} BEFORE {event} ON audit FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='audit is append only'")
