"""生产安全与资源治理。开发模式保持本地运行方式。"""
import os


def enabled():
    mode=os.getenv('ARCSYNC_ENV','development')
    if mode not in ('development','test','production'):
        raise RuntimeError('ARCSYNC_ENV只能为development、test或production')
    return mode=='production'
