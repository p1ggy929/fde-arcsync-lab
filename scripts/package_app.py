"""Package runtime source and documentation, excluding local runtime data and credentials."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
root=Path(__file__).resolve().parents[1]
with ZipFile(root/'dist'/'arcsync-0.1.0.zip','w',ZIP_DEFLATED) as archive:
    names=['src','scripts','config','docs','examples','tests','.github']
    for name in names:
        for path in sorted((root/name).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                archive.write(path,Path('arcsync')/path.relative_to(root))
    for name in ['README.md','LICENSE','CHANGELOG.md','CONTRIBUTING.md','requirements.txt','package.json','.gitignore','data/README.md','discovery-report.md']:
        path=root/name
        if path.exists(): archive.write(path,Path('arcsync')/name)
print('构建完成：dist/arcsync-0.1.0.zip（不含本地数据库、会话和口令配置）')
