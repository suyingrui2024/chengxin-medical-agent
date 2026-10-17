"""Lossless storage compaction of explicitly named inactive generated bundles."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tarfile
import psutil

root = Path(__file__).resolve().parents[1]
out = root / '.runtime/calculation-supplements-20261017'
out.mkdir(exist_ok=True)
archive_dir = root / 'dist/desktop/archives'
archive_dir.mkdir(exist_ok=True)

def snapshot(folder):
    result = {}
    for p in [folder, *sorted(folder.rglob('*'))]:
        rel = str(p.relative_to(folder.parent))
        mode = p.lstat().st_mode
        item = {'mode':stat.S_IMODE(mode)}
        if stat.S_ISLNK(mode): item.update(kind='link',target=os.readlink(p))
        elif stat.S_ISDIR(mode): item.update(kind='directory')
        elif stat.S_ISREG(mode): item.update(kind='file',size=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
        else: raise RuntimeError('Unsupported bundle entry')
        result[rel] = item
    return result

def inactive(folder):
    for process in psutil.process_iter():
        try:
            for value in (process.exe(), process.cwd()):
                if value == str(folder) or value.startswith(str(folder) + '/'):
                    raise RuntimeError('Bundle is in use; expanded copy retained')
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

for version in ['0.10.1','0.10.2']:
    folder = root / f'dist/desktop/Chengxin-{version}-linux-x64'
    archive = archive_dir / (folder.name + '.tar.gz')
    if archive.exists() or not folder.is_dir(): raise RuntimeError('Fresh explicit source/archive required')
    inactive(folder)
    before = snapshot(folder)
    (out / f'old-bundle-{version}-index.json').write_text(json.dumps(before,indent=2)+'\n')
    with tarfile.open(archive,'w:gz',compresslevel=1) as tar:
        tar.add(folder,arcname=folder.name,recursive=True)
    checked = {}
    with tarfile.open(archive,'r:gz') as tar:
        for entry in tar:
            assert entry.name not in checked
            item = {'mode':stat.S_IMODE(entry.mode)}
            if entry.issym(): item.update(kind='link',target=entry.linkname)
            elif entry.isdir(): item.update(kind='directory')
            elif entry.isfile(): item.update(kind='file',size=entry.size,sha256=hashlib.sha256(tar.extractfile(entry).read()).hexdigest())
            else: raise RuntimeError('Unsupported archive entry')
            checked[entry.name] = item
    assert checked == before, 'Archive differs; original retained'
    assert snapshot(folder) == before, 'Original changed; expanded copy retained'
    inactive(folder)
    report = {'version':version,'archive':str(archive.relative_to(root)),'entries':len(before),'archive_bytes':archive.stat().st_size,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'file_content_modes_links_verified':True,'restore':f'tar -xzf {archive.relative_to(root)} -C dist/desktop','expanded_copy_removed':False}
    report_path = out / f'old-bundle-{version}-storage.json'
    report_path.write_text(json.dumps(report,indent=2)+'\n')
    shutil.rmtree(folder)
    report['expanded_copy_removed'] = True
    report_path.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report),flush=True)
