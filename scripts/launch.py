"""One-click local bootstrap; runs with Python's standard library only."""
import argparse
import json
import os
import runpy
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
import webbrowser

ROOT = Path(__file__).resolve().parents[1]


def say(message):
    print(message, flush=True)


def health(url):
    # Local readiness must bypass system HTTP proxies.
    try:
        with build_opener(ProxyHandler({})).open(url + '/api/health', timeout=1) as response:
            data = json.loads(response.read(8192))
            return (data.get('status') == 'ok' and data.get('framework') == 'FastAPI'
                    and data.get('mode') == 'synthetic-local'
                    and data.get('upstream_connected') is False)
    except (URLError, OSError, ValueError):
        return False


def port_in_use(port):
    with socket.socket() as connection:
        connection.settimeout(.5)
        return connection.connect_ex(('127.0.0.1', port)) == 0


def prepare_environment():
    environment = ROOT / '.venv'
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        say('首次启动：正在创建项目独立运行环境…')
        subprocess.run([sys.executable, '-m', 'venv', str(environment)], check=True, cwd=ROOT)
    subprocess.run([str(python), '-c', 'import sys; sys.exit(sys.version_info < (3, 11))'], check=True)
    requirements = ROOT / 'requirements.txt'
    # Reuse matching installed dependencies without contacting the package index.
    verify = '''import importlib.metadata, pathlib, sys
import fastapi, uvicorn, httpx, faiss, pymysql, dotenv
from packaging.requirements import Requirement
for line in pathlib.Path(sys.argv[1]).read_text().splitlines():
    line = line.split('#', 1)[0].strip()
    if not line: continue
    requirement=Requirement(line)
    if importlib.metadata.version(requirement.name) not in requirement.specifier:
        sys.exit(1)
'''
    valid = subprocess.run([str(python), '-c', verify, str(requirements)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not valid:
        say('正在安装或更新项目依赖（需要联网，完成后再次启动可离线运行）…')
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(requirements)], check=True, cwd=ROOT)
    return python


def open_page(url, enabled):
    say('平台入口：' + url)
    if enabled:
        try:
            if not webbrowser.open(url):
                say('浏览器未自动打开，请复制上方地址访问。')
        except webbrowser.Error:
            say('浏览器未自动打开，请复制上方地址访问。')


def stop(process):
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main():
    parser = argparse.ArgumentParser(description='ArcSync 一键启动：检查环境、安装依赖、启动并打开浏览器')
    parser.add_argument('--port', type=int, default=os.environ.get('PORT', '8765'))
    parser.add_argument('--no-browser', action='store_true', help='不自动打开浏览器')
    parser.add_argument('--check', action='store_true', help='只准备并检查运行环境，不启动服务')
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        raise RuntimeError('需要 Python 3.11 或更高版本。')
    if not 1 <= args.port <= 65535:
        raise RuntimeError('端口必须在 1–65535 之间。')
    url = f'http://127.0.0.1:{args.port}'
    browser = not args.no_browser and os.environ.get('ARCSYNC_OPEN_BROWSER', '1') != '0'
    if not args.check and port_in_use(args.port):
        if health(url):
            with build_opener(ProxyHandler({})).open(url+'/api/health',timeout=2) as response:
                running=json.loads(response.read(8192))
            expected=runpy.run_path(str(ROOT/'src'/'arcsync'/'core'/'runtime.py'))['BUILD_ID']
            if running.get('build_id')!=expected:
                raise RuntimeError('检测到平台代码已更新，但端口上仍运行旧服务。请在原启动窗口按 Ctrl+C 停止后重新启动；已保存数据会保留。')
            say('检测到当前版本的 ArcSync 已启动，直接打开现有平台。')
            if os.environ.get('ARCSYNC_DB'):
                say('已有服务沿用其启动时的数据配置；本次 ARCSYNC_DB 不会改变该服务。')
            open_page(url, browser)
            return 0
        raise RuntimeError(f'端口 {args.port} 被其他程序占用。请用 PORT=8877 sh scripts/start.sh 更换端口。')
    python = prepare_environment()
    if args.check:
        say('环境检查通过，项目已具备启动条件。')
        return 0
    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT / 'src') + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    say('正在启动云州档案平台…')
    process = subprocess.Popen([str(python), '-m', 'arcsync.main', '--port', str(args.port)],
                               cwd=ROOT, env=env, start_new_session=os.name != 'nt')
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('服务提前退出，请查看上方错误信息。')
            if health(url):
                break
            time.sleep(.2)
        else:
            raise RuntimeError('服务在 30 秒内未就绪，请查看上方日志。')
        open_page(url, browser)
        say('服务运行中。请保留此窗口；按 Ctrl+C 停止，已保存数据会保留。')
        return process.wait()
    except KeyboardInterrupt:
        say('\n正在停止平台…')
        return 0
    finally:
        stop(process)


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    for name in ('SIGTERM', 'SIGHUP'):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), interrupted)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say('\n已取消启动。')
        sys.exit(0)
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        say(f'启动失败：{error}')
        say('请排查 Python 版本、网络连接及上方安装提示，然后重新双击“启动项目.command”。')
        sys.exit(1)
