"""Recognize existing SC review phrases without broad keyword suppression."""
import json,re,threading,time,uuid
from functools import lru_cache
from pathlib import Path
from runtime_settings import data_path
from atomic_io import append_jsonl

SAVED='复盘文本已保存，请进入软件查看。'
SAVE_KEYS={'ConfirmSaveSettingsAndRaceReport','ConfirmSaveRaceReport','RaceReportSaved','ConfirmSaveSettings'}
START_KEYS={'GreatRace','MediocreRace','CatastrophicRace'}
END_KEYS={'GoodConsistency','MediocreConsistency','BadConsistency'}
PART_KEYS={'Compare2Leader','InvalidCritics','PositiveSummary','GoodPace','MediocrePace','BadPace'}
_lock=threading.Lock()

@lru_cache(maxsize=1)
def patterns():
    entries=json.loads(data_path('phrasebook.json').read_text(encoding='utf-8'))
    result=[]
    for row in entries:
        key=row.get('key','').split('.')[0]
        if key not in SAVE_KEYS and (row.get('role')!='Race Strategist' or key not in START_KEYS|END_KEYS|PART_KEYS):continue
        for template in (row['before'].strip(),row['after'].strip()):
            names=set();parts=[];cursor=0
            for match in re.finditer(r'%([A-Za-z][A-Za-z0-9_]*)%',template):
                parts.append(re.escape(template[cursor:match.start()]))
                name=match[1];parts.append(f'(?P={name})' if name in names else f'(?P<{name}>.*?)');names.add(name);cursor=match.end()
            parts.append(re.escape(template[cursor:]));result.append((re.compile(''.join(parts)),key))
    return result

def classify(text):
    if text.strip() in (SAVED,'本次复盘已经保存。'):return dict(kind='save',key='LocalReviewSaved',values={})
    for pattern,key in patterns():
        match=pattern.fullmatch(text.strip())
        if match:return dict(kind='save' if key in SAVE_KEYS else 'start' if key in START_KEYS else 'end' if key in END_KEYS else 'part',key=key,values=match.groupdict())
    return None

def record_event(root,phrase,event,request_id=None):
    """Small journal shared with telemetry; never invokes speech or game controls."""
    request_id=request_id or uuid.uuid4().hex
    row=dict(phrase,event=event,id=request_id,time=time.time())
    path=Path(root)/'review_native_events.jsonl'
    with _lock:append_jsonl(path,row)
    return request_id
