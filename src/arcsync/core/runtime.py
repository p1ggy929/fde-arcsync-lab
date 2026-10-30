"""Startup source identity; distinguish an old process from updated files on disk."""
import hashlib
from pathlib import Path


def source_fingerprint(root):
    digest=hashlib.sha256()
    for path in sorted((Path(root)/'src').rglob('*')):
        if path.is_file() and path.suffix in ('.py','.js','.css','.html'):
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:20]


BUILD_ID=source_fingerprint(Path(__file__).resolve().parents[3])
