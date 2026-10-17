"""Bind managed services to one desktop process, including abnormal exits."""
import json
import os
from pathlib import Path
import uuid

import psutil


def claim(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    process = psutil.Process(os.getpid())
    record = dict(pid=process.pid, created=process.create_time(),
                  exe=process.exe(), token=uuid.uuid4().hex)
    temporary = root/'app_owner.tmp'
    temporary.write_text(json.dumps(record), encoding='utf-8')
    temporary.replace(root/'app_owner.json')
    return record


def owner_alive(root, token=None):
    try:
        record = json.loads((Path(root)/'app_owner.json').read_text(encoding='utf-8'))
        if token is not None and record.get('token') != token:
            return False
        process = psutil.Process(record['pid'])
        return (abs(process.create_time()-record['created']) < .01
                and Path(process.exe()).resolve() == Path(record['exe']).resolve()
                and process.is_running())
    except (psutil.Error, OSError, ValueError, KeyError, TypeError):
        return False


def release(root, record):
    path = Path(root)/'app_owner.json'
    try:
        if json.loads(path.read_text(encoding='utf-8')) == record:
            path.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass
