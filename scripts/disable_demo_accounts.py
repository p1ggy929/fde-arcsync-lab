"""停服后显式停用演示账号并撤销其会话；保留审计与业务数据。"""
import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from arcsync.core.store import connect, ROLES

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--confirm',action='store_true')
    args=parser.parse_args()
    if not args.confirm:parser.error('停服备份后以--confirm执行；业务样例不会自动删除')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for username in ROLES:
            row=db.execute('SELECT user_id FROM user_accounts WHERE username=?',(username,)).fetchone()
            if not row:continue
            tokens=[r[0] for r in db.execute('SELECT token FROM session_identities WHERE user_id=?',(row[0],))]
            for token in tokens:db.execute('DELETE FROM sessions WHERE token=?',(token,))
            db.execute('DELETE FROM session_identities WHERE user_id=?',(row[0],))
            db.execute('UPDATE user_accounts SET enabled=0 WHERE user_id=?',(row[0],))
    print('演示账号已停用并撤销会话；不要直接将含演示业务的数据库用于生产。')
