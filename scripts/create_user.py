"""本机创建独立用户：密码交互输入，不进入命令行参数或浏览器。"""
import argparse
import getpass
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from arcsync.core.store import init_db, ROLES
from arcsync.core.accounts import create_account
from arcsync.core.service import Problem

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='创建独立人员账号（每个人应使用不同账号）')
    parser.add_argument('--username',required=True)
    parser.add_argument('--name',required=True)
    parser.add_argument('--role',required=True,choices=ROLES)
    args=parser.parse_args()
    try:
        password=getpass.getpass('请输入密码（至少12位）：')
        if password!=getpass.getpass('再次输入密码：'):raise Problem('两次密码不一致')
        init_db();create_account(args.username,args.name,args.role,password)
        print('账号已创建，可从欢迎页登录。')
    except (Problem,KeyboardInterrupt) as error:
        print(getattr(error,'message','已取消创建'),file=sys.stderr);sys.exit(1)
