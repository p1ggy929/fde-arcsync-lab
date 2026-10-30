"""停写后的SQLite一致性备份与校验恢复；不覆盖目标目录。"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import os
from contextlib import closing


def backup(destination,business,chat):
    target=Path(destination);target.mkdir(mode=0o700,parents=True,exist_ok=False)
    manifest={'format':1,'files':{}}
    for name,source in [('business.sqlite3',business),('chat.sqlite3',chat)]:
        source=Path(source).resolve()
        if not source.is_file():raise ValueError('备份源不存在：'+str(source))
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src, closing(sqlite3.connect(target/name)) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('数据库完整性检查失败')
        os.chmod(target/name,0o600)
        manifest['files'][name]=hashlib.sha256((target/name).read_bytes()).hexdigest()
    (target/'manifest.json').write_text(json.dumps(manifest,indent=2))
    return target


def restore(source,destination):
    source=Path(source);manifest=json.loads((source/'manifest.json').read_text())
    if manifest.get('format')!=1 or set(manifest['files'])!={'business.sqlite3','chat.sqlite3'}:raise ValueError('备份清单无效')
    for name,digest in manifest['files'].items():
        if hashlib.sha256((source/name).read_bytes()).hexdigest()!=digest:raise ValueError('备份校验失败')
        with closing(sqlite3.connect((source/name).resolve().as_uri()+'?mode=ro',uri=True)) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('备份数据库无效')
    destination=Path(destination);destination.mkdir(mode=0o700,parents=True,exist_ok=False)
    for name in manifest['files']:
        shutil.copyfile(source/name,destination/name);os.chmod(destination/name,0o600)
    return destination


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='须先停止应用写入；只支持SQLite，不备份密钥与Redis')
    parser.add_argument('action',choices=['backup','restore'])
    parser.add_argument('--destination',required=True)
    parser.add_argument('--source')
    parser.add_argument('--business',default=os.getenv('ARCSYNC_DB','data/arcsync.sqlite3'))
    parser.add_argument('--chat',default=os.getenv('ARCSYNC_CHAT_DB') or os.getenv('ARCSYNC_DB','data/arcsync.sqlite3'))
    parser.add_argument('--writes-stopped',action='store_true',help='确认所有实例已停止写入')
    args=parser.parse_args()
    if not args.writes_stopped:parser.error('请先停止全部实例写入，再指定--writes-stopped')
    if args.action=='restore' and not args.source:parser.error('恢复须指定--source')
    result=backup(args.destination,args.business,args.chat) if args.action=='backup' else restore(args.source,args.destination)
    print('已完成：'+str(result)+'；请按生产文档完成恢复验收')
