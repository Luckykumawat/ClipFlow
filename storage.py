"""Inventory and explicit, terminal-project deletion for the single-owner workspace."""
import shutil
from pathlib import Path

TERMINAL={'ready','failed'}


def size(path):
    try:return path.stat().st_size if path.is_file() and not path.is_symlink() else 0
    except FileNotFoundError:return 0


def files(folder):
    return [p for p in folder.rglob('*') if p.is_file() and not p.is_symlink()] if folder.is_dir() else []


def original_paths(data, job):
    if job.get('upload'):
        name=job['upload']
        if Path(name).name!=name:raise ValueError('Invalid stored upload path')
        return [data/'uploads'/name]
    return list((data/job['id']).glob('source.*'))


def related(jobs, job):
    return [j for j in jobs if j.get('upload')==job['upload']] if job.get('upload') else [job]


def inventory(data, jobs):
    all_files=files(data);used=sum(size(p) for p in all_files)
    originals={p for j in jobs for p in original_paths(data,j)}
    original_bytes=sum(size(p) for p in originals)
    clip_bytes=sum(size(p) for p in all_files if p.name.startswith('clip-') and p.suffix=='.mp4' and p.parent!=data/'uploads')
    project_usage={}
    for job in jobs:
        source_bytes=sum(size(p) for p in original_paths(data,job))
        peers=related(jobs,job)
        project_usage[job['id']]={'original_bytes':source_bytes,'project_bytes':sum(size(p) for p in files(data/job['id'])),
            'original_present':any(p.is_file() for p in original_paths(data,job)),
            'original_shared':bool(job.get('upload') and len(peers)>1),
            'shared_projects':len(peers),
            'can_delete':job['status'] in TERMINAL,
            'can_delete_original':job['status'] in TERMINAL and all(j['status'] in TERMINAL for j in peers)}
    disk=shutil.disk_usage(data)
    return {'used_bytes':used,'original_bytes':original_bytes,'clip_bytes':clip_bytes,
            'other_bytes':max(0,used-original_bytes-clip_bytes),'free_bytes':disk.free,'disk_total_bytes':disk.total,
            'projects':project_usage}


def delete(data, jobs, job, action):
    """Caller holds the workspace lock across this operation and persistence."""
    if job['status'] not in TERMINAL:return 409,{'error':'Wait for this project to finish before deleting files.'}
    if action not in ('original','project'):return 400,{'error':'Choose original or project deletion.'}
    peers=related(jobs,job);freed=0;removed_upload=''
    if action=='original':
        if any(j['status'] not in TERMINAL for j in peers):return 409,{'error':'This original is needed by another queued or processing project. Wait for it to finish.'}
        for path in original_paths(data,job):
            freed+=size(path)
            path.unlink(missing_ok=True)
        for peer in peers:peer['original_deleted']=True
        removed_upload=job.get('upload','')
    else:
        folder=data/job['id'];freed=sum(size(p) for p in files(folder))
        if folder.exists():shutil.rmtree(folder)
        # Shared uploads belong to every referencing project. Remove only the final reference.
        if job.get('upload') and len(peers)==1:
            for path in original_paths(data,job):
                freed+=size(path);path.unlink(missing_ok=True)
            removed_upload=job['upload']
        jobs.remove(job)
    return 200,{'ok':True,'action':action,'freed_bytes':freed,'removed_upload':removed_upload}
