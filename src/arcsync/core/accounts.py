"""具名账号与旧岗位账号的兼容映射，不改变现有业务表。"""
import hashlib
import secrets
import uuid
from .store import ROLES, connect, now
from .service import Problem


def migrate_accounts(db):
    """增加独立用户身份与登录会话映射，原账号口令保持不变。"""
    if getattr(db,'dialect','sqlite')=='mysql':
        statements=[
            'CREATE TABLE IF NOT EXISTS user_accounts(user_id VARCHAR(64) PRIMARY KEY,username VARCHAR(100) UNIQUE NOT NULL,role VARCHAR(32) NOT NULL,name VARCHAR(100) NOT NULL,salt VARCHAR(64) NOT NULL,password VARCHAR(128) NOT NULL,enabled INT NOT NULL DEFAULT 1,created VARCHAR(40) NOT NULL)',
            'CREATE TABLE IF NOT EXISTS session_identities(token VARCHAR(128) PRIMARY KEY,user_id VARCHAR(64) NOT NULL)',
            'CREATE TABLE IF NOT EXISTS document_files(document_id VARCHAR(120),version VARCHAR(64),filename VARCHAR(255) NOT NULL,PRIMARY KEY(document_id,version))',
        ]
        for sql in statements:db.execute(sql+' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin')
    else:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS user_accounts(user_id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,role TEXT NOT NULL,name TEXT NOT NULL,salt TEXT NOT NULL,password TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS session_identities(token TEXT PRIMARY KEY,user_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS document_files(document_id TEXT,version TEXT,filename TEXT NOT NULL,PRIMARY KEY(document_id,version));
        ''')
    for account in db.execute('SELECT * FROM users'):
        identifier=uuid.uuid5(uuid.NAMESPACE_URL,'arcsync:legacy:'+account['role']).hex
        db.execute('INSERT OR IGNORE INTO user_accounts VALUES(?,?,?,?,?,?,?,?)',
                   (identifier,account['role'],account['role'],account['name'],account['salt'],account['password'],1,now()))
    # 仅将没有映射的旧会话归到对应旧账号，后续新会话直接使用人员ID。
    db.execute('INSERT OR IGNORE INTO session_identities(token,user_id) SELECT s.token,u.user_id FROM sessions s JOIN user_accounts u ON u.username=s.role')


def create_account(username,name,role,password):
    """由受控本机工具创建人员账号；不开放公网注册或自行选择管理权限。"""
    if not isinstance(username,str) or not 3<=len(username)<=80 or not all(c.isascii() and (c.isalnum() or c in '._-') for c in username):
        raise Problem('账号须为3～80位英文字母、数字、点、下划线或短横线')
    if role not in ROLES:raise Problem('岗位不存在')
    if not isinstance(name,str) or not 1<=len(name.strip())<=60:raise Problem('姓名须为1～60字')
    if not isinstance(password,str) or not 12<=len(password)<=200:raise Problem('独立账号密码须为12～200位')
    identifier=uuid.uuid4().hex;salt=secrets.token_hex(16)
    hashed=hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),120000).hex()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM user_accounts WHERE username=?',(username,)).fetchone():raise Problem('账号已存在',409)
        db.execute('INSERT INTO user_accounts VALUES(?,?,?,?,?,?,?,?)',(identifier,username,role,name.strip(),salt,hashed,1,now()))
    return identifier
