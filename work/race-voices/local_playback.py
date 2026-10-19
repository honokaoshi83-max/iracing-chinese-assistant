"""Ordinary SC audio delivery waits until local playback has completed."""
import json,os,time,uuid
from pathlib import Path
import psutil

def claim(root,duration):
    process=psutil.Process(os.getpid())
    row=dict(token=uuid.uuid4().hex,pid=process.pid,created=process.create_time(),until=time.time()+duration+2)
    path=Path(root)/'local_playback.json';temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(row),encoding='utf-8');temp.replace(path)
    return row['token']

def active(root):
    try:
        row=json.loads((Path(root)/'local_playback.json').read_text(encoding='utf-8'))
        if row['until']<=time.time():return False
        p=psutil.Process(row['pid'])
        return abs(p.create_time()-row['created'])<.01 and p.is_running()
    except (OSError,ValueError,KeyError,TypeError,psutil.Error):return False

def release(root,token):
    path=Path(root)/'local_playback.json'
    try:
        if json.loads(path.read_text(encoding='utf-8')).get('token')==token:path.unlink(missing_ok=True)
    except (OSError,ValueError):pass
