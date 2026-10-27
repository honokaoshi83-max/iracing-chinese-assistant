"""Bind managed services to one desktop process, including abnormal exits."""
import json
import os
from pathlib import Path
import uuid

import psutil

from atomic_io import release_if_owned, write_json


def claim(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    process = psutil.Process(os.getpid())
    record = dict(pid=process.pid, created=process.create_time(),
                  exe=process.exe(), token=uuid.uuid4().hex)
    write_json(root/'app_owner.json', record)
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
    release_if_owned(Path(root)/'app_owner.json', record)
