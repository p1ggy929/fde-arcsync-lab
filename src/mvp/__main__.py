"""使用 PYTHONPATH=src python -m mvp 启动独立演示。"""
import argparse
import os


def main():
    parser = argparse.ArgumentParser(description='云州档案 MVP')
    parser.add_argument('--port', type=int, default=8766, metavar='1–65535')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('端口必须介于 1 与 65535 之间')

    # 先加载主项目配置，再为本进程指定独立的演示数据库与签名密钥来源。
    from arcsync.core.store import ROOT
    import uvicorn
    from .app import create_app

    os.environ.update(
        ARCSYNC_ENV='development',
        ARCSYNC_DATABASE_URL='',
        ARCSYNC_DB=str(ROOT / 'data' / 'mvp' / 'arcsync.sqlite3'),
        ARCSYNC_DEMO_PASSWORD='ArcSync2026!',
    )
    os.environ.pop('ARCSYNC_JWT_SECRET', None)
    uvicorn.run(create_app(), host='127.0.0.1', port=args.port, proxy_headers=False)


if __name__ == '__main__':
    main()
