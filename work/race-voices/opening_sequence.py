"""One ordered opening per telemetry session, using observed track conditions."""
import io
import json
import math
import re
import subprocess
import wave
import time
import threading
import uuid
from pathlib import Path
from functools import lru_cache
from runtime_settings import data_path,documents
from atomic_io import write_json

ENGINEER='我是克洛琳德，负责赛车状态、油量和维修提醒。'
SPOTTER='我是芙宁娜，比赛中的圈速播报和周围赛车提醒由我负责，我也会留意天气变化与进站策略。'
ACTIVE=Path(__file__).resolve().parent/'opening_active.json'
PRIORITY=ACTIVE.with_name('component_speech_priority.json')
PRIORITY_LOCK=threading.Lock()

def component_marker(path=None):
    path=Path(path) if path is not None else documents()/'Simulator Controller/Temp/Speaking.status'
    try:
        stat=path.stat();return [stat.st_ino,stat.st_mtime_ns,stat.st_size]
    except OSError:return None

def aggregate_pending(state):
    waiting=[r for r in state.get('requests',{}).values() if r['until']>time.time() and r.get('marker')==state.get('marker')]
    state['safety']=state.get('current_safety',False) or any(r['safety'] for r in waiting)
    state['interrupt']=state.get('current_interrupt',False) or any(r['interrupt'] for r in waiting)

def complete_component_request(token):
    with PRIORITY_LOCK:
        try:
            state=json.loads(PRIORITY.read_text(encoding='utf-8'))
            state.get('requests',{}).pop(token,None)
            if state.get('request_id')==token:
                state['playback_until']=time.time()+3
            aggregate_pending(state)
            write_json(PRIORITY,state)
        except (OSError,ValueError,KeyError):pass

def record_component_priority(text,suppressed,pending=False):
    from emotion_profiles import classify
    from button_priority import spotter_safety
    safety=not suppressed and spotter_safety(text)
    urgent=not suppressed and (classify(text)=='warning' or safety)
    with PRIORITY_LOCK:
        try:previous=json.loads(PRIORITY.read_text(encoding='utf-8'))
        except (OSError,ValueError):previous={}
        until=time.time()+(15 if urgent else 3)
        if not urgent:
            if previous.get('urgent') and previous.get('until',0)>time.time():
                urgent=True;until=previous['until']
        token=uuid.uuid4().hex;marker=component_marker()
        requests={key:value for key,value in previous.get('requests',{}).items() if value['until']>time.time()}
        interrupt=not suppressed and (classify(text)=='warning' or safety)
        if pending and not suppressed:requests[token]=dict(safety=safety,interrupt=interrupt,until=time.time()+120,marker=marker)
        state=dict(urgent=urgent,current_safety=safety,current_interrupt=interrupt,suppressed=suppressed,until=until,
                   playback_until=time.time()+120 if pending and not suppressed else until,marker=marker,requests=requests,request_id=token)
        aggregate_pending(state)
        write_json(PRIORITY,state)
        return token

def component_interrupts_opening():
    try:
        state=json.loads(PRIORITY.read_text(encoding='utf-8'))
        if state['until']>time.time():return state['urgent']
    except (OSError,ValueError,KeyError):pass
    # Unknown cached component speech remains eligible for safety priority.
    return True

def component_interrupts_playback(marker_path=None):
    try:
        state=json.loads(PRIORITY.read_text(encoding='utf-8'))
        if state.get('playback_until',state['until'])>time.time():
            if state.get('interrupt',True):return True
            marker=component_marker(marker_path)
            if marker is not None and marker==state.get('marker'):return False
    except (OSError,ValueError,KeyError):pass
    # Unknown cached spotter audio keeps safety priority.
    return True

def mark_opening(active):
    if active:
        write_json(ACTIVE,{'until':time.time()+180})
    else:ACTIVE.unlink(missing_ok=True)

def opening_active():
    try:return json.loads(ACTIVE.read_text(encoding='utf-8'))['until']>time.time()
    except (OSError,ValueError,KeyError):return False

def split_opening_state(text):
    from telemetry_announcer import clean_phrase
    text=clean_phrase(text.strip())
    return bool(re.fullmatch(r'气温[^。]+[度℃][，,。]赛道温度[^。]+[度℃][。.]?',text)
                or text in ('赛道干燥。','赛道抓地状态良好。','赛道橡胶较少，抓地力偏低。'))

class OpeningLine(str):
    def __new__(cls,text,voice):
        line=super().__new__(cls,text)
        line.voice=voice
        return line

def opening_sequence(name,air=None,track=None,wetness=None):
    lines=[OpeningLine('赛车已经准备就绪。','clorinde'),OpeningLine(ENGINEER,'clorinde'),OpeningLine(SPOTTER,'furina')]
    if name:lines.append(OpeningLine('当前赛道是'+name+'。','clorinde'))
    state=[]
    for title,value in [('气温',air),('赛道温度',track)]:
        if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value):
            state.append(title+str(round(value))+'度。')
    # iRacing TrackWetness enum: 0 unknown, 1 dry ... 7 extremely wet.
    condition={1:'赛道干燥。',2:'赛道大部分干燥。',3:'赛道略湿。',4:'赛道有少量积水。',
               5:'赛道湿滑。',6:'赛道有较多积水。',7:'赛道积水严重。'}.get(wetness)
    if condition:state.append(condition)
    if state:lines.append(OpeningLine(''.join(state),'furina'))
    lines.append(OpeningLine('在比赛中如果想要查询赛车状态请按下通讯按钮，祝你比赛顺利。','clorinde'))
    return lines

@lru_cache(maxsize=1)
def greeting_patterns():
    templates={ENGINEER,SPOTTER,'我是芙宁娜，负责圈速、周围赛车、天气和策略提醒。',
               '我是芙宁娜，会帮你留意周围的赛车。','需要帮助随时叫我。',
               '赛车状态和油量监控已就绪。','进站策略监控已就绪。','策略监控也已就绪。'}
    path=data_path('phrasebook.json')
    if path.exists():
        for row in json.loads(path.read_text(encoding='utf-8')):
            if row.get('key','').startswith('Greeting'):
                templates.update((row['before'].strip(),row['after'].strip()))
    for path in data_path('grammars').glob('*.zh'):
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if line.startswith('Greeting') and '=' in line:templates.add(line.split('=',1)[1].strip())
    patterns=[]
    for text in templates:
        parts=re.split(r'(%[A-Za-z][A-Za-z0-9_]*%)',text)
        patterns.append(re.compile(''.join('.+?' if part.startswith('%') and part.endswith('%') else re.escape(part) for part in parts)))
    return patterns

def native_greeting(text):
    return any(pattern.fullmatch(text.strip()) for pattern in greeting_patterns())

@lru_cache(maxsize=2)
def silent_audio(fmt):
    output=io.BytesIO()
    with wave.open(output,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(32000)
        wav.writeframes(b'\0'*3200)
    data=output.getvalue()
    if fmt=='wav':return data
    if fmt!='mp3':raise ValueError('Only wav and mp3 are supported')
    from runtime_settings import ffmpeg_path
    return subprocess.run([str(ffmpeg_path()),'-hide_banner','-loglevel','error','-i','pipe:0',
                           '-f','mp3','pipe:1'],input=data,stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,check=True).stdout
