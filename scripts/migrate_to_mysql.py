"""Explicit offline copy into an EMPTY MySQL database; never overwrites a target."""
import argparse
import os
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from arcsync.data_models.config import database_kind
from arcsync.data_models.mysql import MySQLConnection, initialize

TABLES=('users','user_accounts','document_files','documents','businesses','handoffs','issues','events','audit','feedback','settings',
        'document_chunks','vector_embeddings','agent_runs','agent_steps','access_cases','access_validations','access_drafts','business_requesters','agent_tool_audit','agent_harness_runs','agent_decision_logs','agent_confirmations','agent_run_owners')

def migrate(source,destination):
    path=Path(source).resolve()
    if not path.is_file():raise RuntimeError('源 SQLite 文件不存在')
    with MySQLConnection(destination) as target:
        initialize(target)
        from arcsync.agents.access import initialize as initialize_access
        initialize_access(target)
        from arcsync.agents.harness_store import initialize as initialize_harness
        initialize_harness(target)
        if any(target.execute(f'SELECT COUNT(*) FROM `{table}`').fetchone()[0] for table in (*TABLES,'sessions')):
            raise RuntimeError('目标库包含数据，已停止；请改用全新空库，不覆盖现有记录')
        # No login sessions are copied. All users must sign in again after switching.
        origin=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
        try:
            origin.execute('BEGIN')
            available={r[0] for r in origin.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            counts={}
            for table in TABLES:
                if table not in available:continue
                cursor=origin.execute(f'SELECT * FROM "{table}"')
                columns=[r[0] for r in cursor.description]
                names=','.join('`'+c+'`' for c in columns)
                placeholders=','.join('?' for _ in columns)
                rows=cursor.fetchall()
                target.executemany(f'INSERT INTO `{table}` ({names}) VALUES({placeholders})',rows)
                counts[table]=len(rows)
            return counts
        finally:origin.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='将 SQLite 复制到空 MySQL；执行前先停止平台并备份源文件')
    parser.add_argument('--source',default=str(ROOT/'data'/'arcsync.sqlite3'))
    args=parser.parse_args()
    try:
        url=os.getenv('ARCSYNC_DATABASE_URL','')
        if not url.startswith('mysql'):raise RuntimeError('请先在 .env 设置 ARCSYNC_DATABASE_URL')
        counts=migrate(args.source,url)
        print('复制完成：'+', '.join(f'{table}={count}' for table,count in counts.items()))
        print('源 SQLite 保留；会话不迁移。下次启动使用 MySQL，所有账号需要重新登录。')
    except RuntimeError as error:
        print('迁移已停止：'+str(error),file=sys.stderr)
        sys.exit(1)
    except Exception:
        print('迁移未完成。请检查目标为空库、连接配置、建表/触发器权限及源文件；不会覆盖已有目标数据。',file=sys.stderr)
        sys.exit(1)
