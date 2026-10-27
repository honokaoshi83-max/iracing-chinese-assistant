"""Protected manual-speech scheduling; no controller settings or game input writes."""
import json,os,queue,threading,time
from contextlib import contextmanager
from pathlib import Path
from atomic_io import release_if_owned,write_json

def spotter_safety(text):
    return (text.strip() in ('保持线路。','保持线路') or any(word in text for word in ('蓝旗','快车','三车并排','三辆车并排')) or
            (any(side in text for side in ('左侧','右侧','两侧','旁边','左后方','右后方')) and
             any(word in text for word in ('有车','还有车','无车','并排','净空'))))

def active_spotter_safety():
    from opening_sequence import PRIORITY
    try:
        row=json.loads(PRIORITY.read_text(encoding='utf-8'))
        return bool(row.get('safety')) and row.get('until',0)>time.time()
    except (OSError,ValueError,TypeError):return False

class SpeechQueue(queue.PriorityQueue):
    """Background capacity plus one manual and four safety slots; never evict."""
    def __init__(self,capacity=16):
        super().__init__(maxsize=0);self.capacity=capacity
    def put(self,item,block=True,timeout=None):
        with self.not_full:
            manual=bool(item[-1])
            safety=not manual and item[0]<-1
            if (manual and any(x[-1] for x in self.queue)) or (safety and sum(not x[-1] and x[0]<-1 for x in self.queue)>=4) or (not manual and not safety and sum(not x[-1] and x[0]>=0 for x in self.queue)>=self.capacity):raise queue.Full
            self._put(item);self.unfinished_tasks+=1;self.not_empty.notify()

class PriorityLock:
    """A running model call finishes safely; safety then manual calls go next."""
    def __init__(self):self.condition=threading.Condition();self.busy=False;self.waiting_manual=0;self.waiting_safety=0
    @contextmanager
    def hold(self,manual=False,safety=False):
        with self.condition:
            if manual:self.waiting_manual+=1
            if safety:self.waiting_safety+=1
            try:
                while self.busy or (not safety and self.waiting_safety) or (not safety and not manual and self.waiting_manual):self.condition.wait()
                self.busy=True
            finally:
                if manual:self.waiting_manual-=1
                if safety:self.waiting_safety-=1
        try:yield
        finally:
            with self.condition:self.busy=False;self.condition.notify_all()

class AudioOwner:
    """An interrupted old job cannot purge a newer acknowledgement/summary."""
    def __init__(self):self.lock=threading.Lock();self.generation=0
    def play(self,play,allowed=lambda:True):
        with self.lock:
            if not allowed():return None
            self.generation+=1;play();return self.generation
    def stop(self,generation,stop):
        with self.lock:
            if generation is not None and generation==self.generation:
                stop();self.generation+=1

def reserve_manual(root,owner,now=None):
    now=time.time() if now is None else now
    write_json(Path(root)/'manual_speech_active.json',dict(owner=owner,pid=os.getpid(),expires=now+150))

def manual_active(root,now=None):
    try:
        row=json.loads((Path(root)/'manual_speech_active.json').read_text(encoding='utf-8'))
        now=time.time() if now is None else now
        if not (type(row.get('expires')) in (int,float) and now<row['expires']<=now+150):return False
        import psutil
        return type(row.get('pid')) is int and psutil.pid_exists(row['pid'])
    except (OSError,ValueError,TypeError):return False

def release_manual(root,owner):
    release_if_owned(Path(root)/'manual_speech_active.json',owner,'owner')

class ManualPreemption(Exception):pass

class SynthesisInbox:
    """Keep in-flight audio while the playback scheduler switches to manual work."""
    def __init__(self):
        from concurrent.futures import ThreadPoolExecutor
        self.pool=ThreadPoolExecutor(max_workers=2)
        self.manual_pool=ThreadPoolExecutor(max_workers=1)
        self.safety_pool=ThreadPoolExecutor(max_workers=1)
        self.pending={}
    def response(self,key,fetch,preempted,manual=False,safety=False):
        for old,(future,created) in list(self.pending.items()):
            if old!=key and future.done() and time.monotonic()-created>180:self.pending.pop(old,None)
        # Background HTTP responses may wait on the manual reservation in bridge.
        # A dedicated worker lets the manual HTTP call bypass that wait safely.
        pool=self.safety_pool if safety else self.manual_pool if manual else self.pool
        if key not in self.pending:self.pending[key]=(pool.submit(fetch),time.monotonic())
        future,_=self.pending[key]
        while not future.done():
            if preempted():raise ManualPreemption()
            time.sleep(.03)
        if preempted():raise ManualPreemption()
        self.pending.pop(key,None);return future.result()
