"""Ordinary SC audio delivery waits until local playback has completed."""
import json,os,time,uuid
from pathlib import Path
import psutil
from atomic_io import release_if_owned,write_json

def claim(root,duration):
    process=psutil.Process(os.getpid())
    row=dict(token=uuid.uuid4().hex,pid=process.pid,created=process.create_time(),until=time.time()+duration+2)
    write_json(Path(root)/'local_playback.json',row)
    return row['token']

def active(root):
    try:
        row=json.loads((Path(root)/'local_playback.json').read_text(encoding='utf-8'))
        if row['until']<=time.time():return False
        p=psutil.Process(row['pid'])
        return abs(p.create_time()-row['created'])<.01 and p.is_running()
    except (OSError,ValueError,KeyError,TypeError,psutil.Error):return False

def release(root,token):
    release_if_owned(Path(root)/'local_playback.json',token,'token')
