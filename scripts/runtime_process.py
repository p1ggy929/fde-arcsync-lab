"""Inspect only this user's ArcSync listener before a graceful local upgrade."""
import ctypes
import os
from pathlib import Path
import re
import signal
import struct
import subprocess
import sys
import time


def inspect_server(port,root):
    if sys.platform not in ('darwin','linux'):raise RuntimeError('当前系统不能自动核验旧服务，请在原窗口停止服务后重试。')
    result=subprocess.run(['lsof','-nP','-t',f'-iTCP:{port}','-sTCP:LISTEN'],capture_output=True,text=True,check=False)
    pids={int(value) for value in result.stdout.split() if value.isdigit()}
    if len(pids)!=1:raise RuntimeError('不能唯一确认旧服务进程，未停止任何程序。请在原启动窗口停止服务后重试。')
    pid=pids.pop()
    uid=subprocess.check_output(['ps','-p',str(pid),'-o','uid='],text=True).strip()
    command=subprocess.check_output(['ps','-p',str(pid),'-o','command='],text=True).strip()
    cwd=subprocess.check_output(['lsof','-a','-p',str(pid),'-d','cwd','-Fn'],text=True)
    paths=[line[1:] for line in cwd.splitlines() if line.startswith('n')]
    expected=rf'(?:^|\s)-m\s+arcsync\.main\s+--port\s+{port}(?:\s|$)'
    if uid!=str(os.getuid()) or not re.search(expected,command) or len(paths)!=1 or Path(paths[0]).resolve()!=Path(root).resolve():
        raise RuntimeError('端口上的服务无法确认为当前用户的本项目进程，未自动停止。请检查端口或更换端口启动。')
    if sys.platform=='linux':
        items=Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
    else:
        libc=ctypes.CDLL(None,use_errno=True);mib=(ctypes.c_int*3)(1,49,pid)
        size=ctypes.c_size_t(1024*1024);buffer=ctypes.create_string_buffer(size.value)
        if libc.sysctl(mib,3,buffer,ctypes.byref(size),None,0)!=0:raise RuntimeError('无法保留旧服务配置，未自动重启。请在原窗口停止后重试。')
        data=buffer.raw[:size.value];argc=struct.unpack('i',data[:4])[0]
        position=data.index(b'\0',4)+1
        while data[position:position+1]==b'\0':position+=1
        for _ in range(argc):position=data.index(b'\0',position)+1
        items=data[position:].split(b'\0')
    environment={os.fsdecode(key):os.fsdecode(value) for item in items if b'=' in item for key,value in [item.split(b'=',1)]}
    if not environment:raise RuntimeError('无法保留旧服务配置，未停止现有服务。')
    return pid,environment


def stop_verified_server(pid):
    # No forced kill: let requests and database transactions finish safely.
    os.kill(pid,signal.SIGINT)
    for _ in range(100):
        try:os.kill(pid,0)
        except ProcessLookupError:return
        time.sleep(.1)
    raise RuntimeError('旧服务正在完成当前请求，请稍候再次启动；没有强制终止或删除数据。')
