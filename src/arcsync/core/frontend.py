"""One frontend generation for every account, including lazy-loaded chat markup."""
import hashlib
from html import escape
from pathlib import Path
import re
from .store import ROOT

FRONTEND=ROOT/'src'/'frontend'


def frontend_version():
    digest=hashlib.sha256()
    for path in sorted(FRONTEND.iterdir()):
        if path.is_file() and path.suffix in ('.html','.js','.css','.svg'):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:20]


def version_document(html):
    version=frontend_version()
    # Only our local static assets; untrusted document text is never passed here.
    html=re.sub(r'((?:src|href)=")(/[^"?]+\.(?:js|css))(?:\?[^\"]*)?(\")',
                lambda match:match[1]+match[2]+'?v='+version+match[3],html)
    marker='<meta name="arcsync-ui-version" content="'+escape(version)+'">'
    bootstrap='<script defer src="/frontend-sync.js?v='+version+'"></script>'
    # The version guard is registered before any page controllers execute.
    return html.replace('<head>','<head>'+marker+bootstrap,1)
