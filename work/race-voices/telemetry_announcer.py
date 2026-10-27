"""Read-only iRacing SDK lap/track announcements; standard spotter takes priority."""
import json
import math
import queue
import re
import threading
import time
from functools import lru_cache
from itertools import count
from pathlib import Path

ROOT = Path(__file__).resolve().parent
from runtime_settings import documents, data_path, service_url
from atomic_io import append_jsonl, write_json
from opening_sequence import OpeningLine,opening_sequence,mark_opening,component_interrupts_opening,component_interrupts_playback
SC_TEMP = documents()/'Simulator Controller/Temp'

@lru_cache(maxsize=1)
def phrasebook():
    """Adapt already-loaded SC phrases until assistants reload user grammars.

    Match a complete known template. Never invent a missing telemetry value.
    """
    path = data_path('phrasebook.json')
    if not path.exists():
        return []
    rules, seen = [], set()
    token = r'%([A-Za-z][A-Za-z0-9_]*)%'
    for entry in json.loads(path.read_text(encoding='utf-8')):
        before, after = entry['before'].strip(), entry['after'].strip()
        names = re.findall(token, before)
        if not set(re.findall(token, after)) <= set(names):
            continue
        if (before, after) in seen:
            continue
        seen.add((before, after))
        parts, cursor, captured = [], 0, set()
        for match in re.finditer(token, before):
            parts.append(re.escape(before[cursor:match.start()]))
            name = match.group(1)
            parts.append(f'(?P={name})' if name in captured else f'(?P<{name}>.+?)')
            captured.add(name)
            cursor = match.end()
        parts.append(re.escape(before[cursor:]))
        rules.append((re.compile(''.join(parts)), after))
    return rules

def clean_phrase(text):
    # Recover only the known corrupted farewell, never synthesize INI keys.
    if ('Bye.2=' in text and 'Bye.3=' in text) or text.startswith('鏈椹'):
        return '本次比赛结束，期待下一次合作。'
    for pattern, replacement in phrasebook():
        match = pattern.fullmatch(text)
        if match:
            text = re.sub(r'%([A-Za-z][A-Za-z0-9_]*)%',
                          lambda m: match.group(m.group(1)), replacement)
            break
    text = re.sub(r'\s*(?:Jona|Elisa|Khato|Aiden)\s*', '芙宁娜', text)
    text = text.replace('我是芙宁娜，会帮你留意赛车状态和油量。', '赛车状态和油量监控已就绪。')
    text = text.replace('我是芙宁娜，会帮你留意进站策略。', '进站策略监控已就绪。')
    # Assistants may still hold an older grammar until their next session.
    for before, after in [('两侧都清了。', '两侧已无车。'),
                          ('左侧清了。', '左侧已无车。'),
                          ('右侧清了。', '右侧已无车。')]:
        text = text.replace(before, after)
    text = re.sub(r'(\d+)\s*分\s*(\d+(?:\.\d+)?)\s*秒',
                  lambda m: lap_phrase(int(m.group(1))*60+float(m.group(2)))[2:-1], text)
    prefix = r'赛道(?:是|抓地状态[：:])\s*'
    text = re.sub(prefix+r'(?:绿色|Green)[。.]?', '赛道橡胶较少，抓地力偏低。', text, flags=re.I)
    text=re.sub(prefix+r'Fast[。.]?', '赛道抓地状态良好。', text, flags=re.I)
    from driver_numbers import normalize_references
    return normalize_references(text)

def chinese_number(number):
    from speech_reading import cardinal
    return cardinal(number)

def lap_phrase(seconds):
    minutes, remainder = divmod(round(seconds*10), 600)
    whole, tenth = divmod(remainder, 10)
    whole_text = ('零' if whole < 10 else '')+chinese_number(whole)
    return f'本圈{chinese_number(minutes)}分{whole_text}秒{chinese_number(tenth)}。'

class TrackCatalog:
    def __init__(self, path):
        self.rows = json.loads(Path(path).read_text(encoding='utf-8'))['tracks']
        self.paths = {self.key(path): r['zh'] for r in self.rows for path in [r['path'],*r.get('path_aliases',[])]}
        self.names = {r['en'].casefold(): r['zh'] for r in self.rows}

    @staticmethod
    def key(value):
        return re.sub(r'[\\/\s]+', '/', str(value).strip()).strip('/').casefold()

    def resolve(self, identifier, display, layout=''):
        key = self.key(identifier)
        # Never discard an unknown layout by silently returning the base track.
        if key in self.paths and ('/' in key or not layout):
            return self.paths[key]
        combined = (display + ' - ' + layout).casefold()
        if combined in self.names:
            return self.names[combined]
        if layout and self.key(str(identifier)+'/'+layout) in self.paths:
            return self.paths[self.key(str(identifier)+'/'+layout)]
        base = self.names.get(display.casefold(), self.paths.get(key, display))
        return base + (' — '+layout if layout else '')

def lap_reporting_allowed(session_type,session_state):
    return session_type != "Race" or session_state in (4,5,6)

class LapIncidents:
    """Keep incident evidence against SDK current lap, not score delivery time."""
    def __init__(self):
        self.lap=None
        self.count=None
        self.invalid=set()
        self.current_time=None

    def update(self, lap, incidents, completed=None, current_time=None, last_score=None):
        if self.lap is not None and (lap<self.lap or incidents<self.count):
            self.invalid.clear()
        reset=(completed==0 and (last_score is None or last_score<=0)
               and current_time is not None and self.current_time is not None
               and self.current_time>1 and current_time+1<self.current_time)
        if self.count is not None and incidents>self.count:
            # A crossing sample belongs to the lap just completed.
            target=self.lap if self.lap is not None and lap>self.lap else lap
            self.invalid.add(target)
        if reset:
            # Some sessions keep the same Lap value while leaving the outlap.
            self.invalid.discard(lap)
        self.lap,self.count,self.current_time=lap,incidents,current_time
        self.invalid={n for n in self.invalid if n>=lap-3}

    def valid(self, completed):
        return completed not in self.invalid


def play_with_priority(play, stop, speaking, duration, created, limit, opening=False,
                       is_status=False, on_interrupt=lambda:None, now=None,
                       sleep=None,cancelled=lambda:False,interrupt_delay=None):
    now=now or time.monotonic
    sleep=sleep or time.sleep
    attempts=40 if opening else 2
    for attempt in range(attempts):
        quiet_since=now()
        while now()-created<limit:
            if cancelled():stop();return False
            if speaking():quiet_since=now()
            elif not opening or now()-quiet_since>=2:break
            sleep(.1)
        if cancelled() or now()-created>=limit:return False
        play()
        until=now()+duration
        interrupted=False
        speaking_since=None
        while now()<until:
            if cancelled():stop();return False
            if speaking():
                if speaking_since is None:speaking_since=now()
                delay=interrupt_delay() if interrupt_delay is not None else (.35 if opening else 0)
                if now()-speaking_since>=delay:
                    stop();interrupted=True;on_interrupt();break
            else:speaking_since=None
            sleep(.1)
        if not interrupted:return True
        if is_status:return False
    return False

class LapTracker:
    def __init__(self):
        self.completed = None
        self.last_time = 0
        self.pending = None
        self.best = None
        self.early_score = None
        self.current_time = None
        self.crossing = None

    def update(self, completed, seconds, now, valid=True, current_lap=None, score_lap=None, score_seconds=None):
        previous_timer=self.current_time;self.current_time=current_lap
        if (previous_timer is not None and current_lap is not None
                and previous_timer>1 and current_lap+1<previous_timer):
            self.crossing=(completed if self.completed is not None and completed>self.completed else completed+1,now)
        if self.completed is None or completed < self.completed:
            self.completed, self.last_time, self.pending = completed, seconds, None
            self.best = seconds if completed>0 and valid and seconds>0 else None
            self.early_score = None
            return None
        if completed > self.completed:
            self.pending = (completed, now, valid)
            self.completed = completed
        if not self.pending:
            if (seconds>0 and seconds!=self.last_time and self.crossing is not None
                    and self.crossing[0]==completed+1 and 0<=now-self.crossing[1]<=5):
                self.early_score=(completed+1,seconds,now)
            self.last_time=seconds
            return None
        if now-self.pending[1]>5:
            self.pending=None
            self.last_time=seconds
            return None
        published=(score_lap==completed and isinstance(score_seconds,(int,float))
                   and math.isfinite(score_seconds) and score_seconds>0)
        if published:seconds=score_seconds
        if not math.isfinite(seconds) or seconds <= 0:
            return None
        lap, started, valid = self.pending
        # SDK lap number and last-time variables can update on different ticks.
        # A timer reset proves a crossing, not publication of the new score.
        # Identical values have no freshness evidence; expire instead of guessing.
        early=self.early_score
        early_fresh=early is not None and early[0]==lap and early[1]==seconds and 0<=now-early[2]<=5
        if seconds == self.last_time and not early_fresh and not published:
            return None
        self.pending = None
        self.early_score = None
        self.last_time = seconds
        message = lap_phrase(seconds)
        if not valid:
            message += '本圈有事故记录。'
        else:
            if self.best is not None:
                delta = seconds-self.best
                if delta < -.05:
                    message += f'刷新最快圈，快{-delta:.1f}秒。'
                elif delta > .05:
                    message += f'比最快圈慢{delta:.1f}秒。'
            self.best = seconds if self.best is None else min(self.best, seconds)
        return message

def log(event, **data):
    try:append_jsonl(ROOT/'telemetry_events.jsonl',{'time':time.strftime('%Y-%m-%d %H:%M:%S'),'event':event,**data})
    except OSError:pass

class Announcer:
    def __init__(self):
        from button_priority import SpeechQueue,AudioOwner,SynthesisInbox
        self.items = SpeechQueue(capacity=16)
        self.audio_owner=AudioOwner();self.synthesis=SynthesisInbox();self.manual_pending=threading.Event()
        self._sequence=count()
        self.urgent_pending=threading.Event()
        self.safety_pending=threading.Event()
        self.safety_active=threading.Event()
        self.opening_generation=0
        mark_opening(False)
        self.error_latched = False
        self.status_pending = False
        self.status_lock = threading.Lock()
        from status_prompt import LocalStatusPrompt, prompt_path
        self.status_prompt = LocalStatusPrompt(prompt_path(), SC_TEMP/'Speaking.status', ROOT)
        self.status_ack_until = 0
        threading.Thread(target=self.play, daemon=True).start()

    def audio_state(self, error=None):
        if error is None:
            if self.error_latched:
                log('audio_recovered')
            self.error_latched = False
        else:
            if not self.error_latched:
                log('audio_error', error=type(error).__name__, detail=str(error)[:250])
            self.error_latched = True

    def submit(self, text):
        try:
            from button_priority import spotter_safety
            safety=spotter_safety(text) and not isinstance(text,OpeningLine)
            urgent=getattr(text,'flag',False)
            sequence=self.__dict__.setdefault('_sequence',count())
            item=(-2 if safety else 0 if urgent else 1,(time.monotonic(),next(sequence)),text,False)
            self.items.put_nowait(item)
            if safety:self.__dict__.setdefault('safety_pending',threading.Event()).set()
            if urgent:self.urgent_pending.set()
            log('queued', text=text,order=item[1][1])
        except queue.Full:
            log('queue_full', text=text)

    def cancel_opening(self):
        self.opening_generation+=1
        mark_opening(False)

    def submit_opening(self,lines):
        self.cancel_opening()
        mark_opening(True)
        for index,line in enumerate(lines):
            line.generation=self.opening_generation
            line.last=index==len(lines)-1
            self.submit(line)

    def submit_status(self,text):
        with self.status_lock:
            if self.status_pending:
                log('status_request_busy')
                return False
            try:
                sequence=self.__dict__.setdefault('_sequence',count())
                self.status_pending=True
                manual=getattr(self,'manual_pending',None)
                if manual is not None:
                    manual.set()
                    from button_priority import reserve_manual
                    self.manual_owner=str(time.monotonic_ns())
                    try:reserve_manual(ROOT,self.manual_owner)
                    except OSError as error:log('manual_reservation_error',detail=str(error)[:100])
                prompt=getattr(self,'status_prompt',None)
                self.status_ack_until=0
                # Worker takes status_lock before playback, so acknowledgement
                # still starts first. Reject unavailable slots without a false ACK.
                self.items.put_nowait((-1,(time.monotonic(),next(sequence)),text,True))
                if prompt:
                    from button_priority import AudioOwner
                    owner=self.__dict__.setdefault('audio_owner',AudioOwner())
                    def acknowledge():self.status_ack_until=prompt.play(force=True)
                    def can_acknowledge():
                        return not any(getattr(self,name,threading.Event()).is_set() for name in ('safety_pending','safety_active'))
                    try:owner.play(acknowledge,allowed=can_acknowledge)
                    except Exception as error:log('status_ack_error',detail=str(error)[:150])
                log('status_acknowledged' if self.status_ack_until else 'status_ack_skipped')
                log('status_queued',text=text)
                return True
            except queue.Full:
                self.status_pending=False
                if manual is not None:manual.clear()
                from button_priority import release_manual
                release_manual(ROOT,getattr(self,'manual_owner',None))
                log('status_queue_full')
                return False

    def play(self):
        import requests, winsound, wave
        from button_priority import AudioOwner,SynthesisInbox,active_spotter_safety
        self.__dict__.setdefault('audio_owner',AudioOwner())
        self.__dict__.setdefault('synthesis',SynthesisInbox())
        self.__dict__.setdefault('manual_pending',threading.Event())
        self.__dict__.setdefault('safety_active',threading.Event())
        audio = ROOT/'telemetry_playback.wav'
        while True:
            item=self.items.get()
            if len(item)==3:priority=1;created,text,is_status=item
            else:priority,created,text,is_status=item
            queue_created=created
            if isinstance(created,tuple):created=created[0]
            if priority==0:self.urgent_pending.clear()
            if priority==-2:
                self.safety_active.set()
                getattr(self,'safety_pending',threading.Event()).clear()
            # A long requested summary may require uncached CPU synthesis.
            opening=isinstance(text,OpeningLine) or text.startswith("当前赛道是")
            cancelled=lambda:isinstance(text,OpeningLine) and getattr(text,'generation',self.opening_generation)!=self.opening_generation
            urgent_event=getattr(self,'urgent_pending',None)
            manual_event=getattr(self,'manual_pending',None)
            safety_event=getattr(self,'safety_pending',None)
            preempted=lambda:(priority>-2 and safety_event is not None and safety_event.is_set()) or (not is_status and priority>=0 and manual_event is not None and manual_event.is_set()) or (priority>0 and urgent_event is not None and urgent_event.is_set())
            speaking=lambda:(SC_TEMP/'Speaking.status').exists() and (not is_status or active_spotter_safety())
            audio_token=[None]
            playback_token=[None]
            limit=120 if is_status else (180 if opening or getattr(text,'review',False) else 25)
            try:
                if cancelled():
                    log('opening_cancelled',text=text);continue
                if is_status:
                    # Submission holds this lock until local acknowledgement starts.
                    with self.status_lock:ack_until=self.status_ack_until
                else:ack_until=0
                while speaking() and time.monotonic()-created < limit-5:
                    if cancelled() or preempted():break
                    time.sleep(.1)
                if preempted():
                    from button_priority import ManualPreemption
                    raise ManualPreemption()
                if cancelled():
                    log('opening_cancelled',text=text);continue
                if time.monotonic()-created >= limit-5:
                    log('expired', text=text)
                    continue
                payload={
                    'voice':'furina' if text.startswith('本圈') else getattr(text,'voice','clorinde'), 'input':text, 'speed':1.0,
                    'response_format':'wav','opening':opening,'flag':getattr(text,'flag',False),'review':getattr(text,'review',False),'manual':is_status,'local':True}
                response=self.synthesis.response((str(text),opening,is_status),lambda payload=payload:requests.post(service_url()+'/v1/audio/speech',json=payload,timeout=90),preempted,manual=is_status,safety=priority==-2)
                response.raise_for_status()
                self.audio_state()
                audio.write_bytes(response.content)
                with wave.open(str(audio)) as w:
                    # FFmpeg pipe WAVs use an unknown-length header; count the
                    # actual PCM bytes instead of trusting that sentinel.
                    pcm = w.readframes(min(w.getnframes(), w.getframerate()*300))
                    channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
                    duration = len(pcm)/(channels*width*rate)
                with wave.open(str(audio), 'wb') as w:
                    w.setnchannels(channels)
                    w.setsampwidth(width)
                    w.setframerate(rate)
                    w.writeframes(pcm)
                while time.monotonic()<ack_until and not speaking():
                    time.sleep(.02)
                while speaking() and time.monotonic()-created < limit:
                    if cancelled() or preempted():break
                    time.sleep(.1)
                if time.monotonic()-created >= limit:
                    log('expired_after_synthesis', text=text)
                    continue
                def start_audio():
                    def begin():
                        from local_playback import claim
                        playback_token[0]=claim(ROOT,duration)
                        winsound.PlaySound(str(audio),winsound.SND_FILENAME|winsound.SND_ASYNC)
                    audio_token[0]=self.audio_owner.play(begin,allowed=lambda:not preempted())
                def stop_audio():
                    from local_playback import release
                    release(ROOT,playback_token[0])
                    self.audio_owner.stop(audio_token[0],lambda:winsound.PlaySound(None,winsound.SND_PURGE))
                played=play_with_priority(
                    start_audio,
                    stop_audio,
                    lambda:speaking() and (component_interrupts_opening() if opening else component_interrupts_playback(SC_TEMP/'Speaking.status')),duration,created,limit,
                    opening=opening,is_status=is_status,
                    interrupt_delay=lambda:0 if active_spotter_safety() else .35,
                    on_interrupt=lambda:log('yield_to_spotter',text=text),cancelled=lambda:cancelled() or preempted())
                log('played' if played else 'playback_not_completed',text=text,duration=round(duration,2))
                if not played and preempted() and not is_status and not cancelled():
                    self.items.put_nowait((priority,queue_created,text,is_status))
            except Exception as e:
                from button_priority import ManualPreemption
                if isinstance(e,ManualPreemption):
                    log('status_yield_to_safety' if is_status else 'yield_to_priority',text=text)
                    if not is_status:
                        try:self.items.put_nowait((priority,queue_created,text,is_status))
                        except queue.Full:log('resume_queue_full',text=text)
                else:self.audio_state(e)
            finally:
                from local_playback import release
                release(ROOT,playback_token[0])
                if priority==-2:self.safety_active.clear()
                if isinstance(text,OpeningLine) and getattr(text,'last',False) and not cancelled() and not preempted():
                    mark_opening(False)
                if is_status:
                    with self.status_lock:
                        self.status_pending=False
                        self.manual_pending.clear()
                        from button_priority import release_manual
                        release_manual(ROOT,getattr(self,'manual_owner',None))
                self.items.task_done()

def run():
    import irsdk
    from driver_numbers import write_snapshot,clear_snapshot
    from flag_monitor import FlagMonitor,mark_active
    flags_monitor=FlagMonitor()
    flags_status_at=0
    driver_numbers_at=0
    from damage_monitor import DamageMonitor, sdk_snapshot
    from tire_monitor import TireMonitor, tire_snapshot, load_settings
    from status_button import StatusButton
    from status_report import FuelHistory, snapshot as report_snapshot, status_phrase, DISCONNECTED
    ir = irsdk.IRSDK()
    catalog = TrackCatalog(data_path('tracks_zh.json'))
    announcer = Announcer()
    from session_review import ReviewCoordinator,snapshot as review_snapshot,simulator_is_running
    from runtime_settings import project_root
    reviews=ReviewCoordinator(project_root(),announcer.submit)
    def review_action(method,*args):
        try:return getattr(reviews,method)(*args)
        except Exception as error:
            log('review_error',detail=str(error)[:200])
    from button_dispatch import StatusSnapshot,StatusDispatcher
    button = StatusButton(on_event=log)
    button_snapshot=StatusSnapshot()
    StatusDispatcher(button,button_snapshot,announcer.submit_status,log=log).start()
    fuel_history = FuelHistory()
    session = None
    tracker = LapTracker()
    damage = DamageMonitor()
    try:
        tires = TireMonitor(load_settings())
    except (ValueError, TypeError, OSError):
        log('tire_config_invalid')
        tires = TireMonitor()
    tire_status_at = 0
    lap_incidents = LapIncidents()
    status = ROOT/'telemetry_status.json'
    log('started')
    while True:
        try:
            if not ir.is_initialized:
                ir.startup()
            if not ir.is_initialized or not ir.is_connected:
                button_snapshot.update(DISCONNECTED,False)
                review_action('disconnect',time.time(),simulator_is_running())
                review_action('tick',time.time())
                if session is not None:
                    log('disconnected')
                    announcer.cancel_opening()
                session = None
                clear_snapshot()
                mark_active(False)
                fuel_history = FuelHistory()
                status.write_text(json.dumps({'state':'waiting_for_iracing'}), encoding='utf-8')
                (ROOT/'tire_monitor_status.json').write_text(json.dumps({'state':'waiting_for_iracing','mode':'pit_update_inspection'},ensure_ascii=False),encoding='utf-8')
                ir.shutdown()
                time.sleep(2)
                continue
            ir.freeze_var_buffer_latest()
            weekend = ir['WeekendInfo'] or {}
            key = (weekend.get('SubSessionID'), weekend.get('TrackID'), ir['SessionNum'])
            completed = int(ir['LapCompleted'] or 0)
            seconds = float(ir['LapLastLapTime'] or 0)
            incidents = int(ir['PlayerCarMyIncidentCount'] or 0)
            if key != session:
                session, tracker, lap_incidents = key, LapTracker(), LapIncidents()
                damage = DamageMonitor()
                fuel_history = FuelHistory()
                driver_info = ir['DriverInfo'] or {}
                write_snapshot(driver_info,ir['CarIdxClassPosition'])
                driver_numbers_at=time.monotonic()
                flags_monitor=FlagMonitor()
                from flag_monitor import PlayerFinish
                finish_monitor=PlayerFinish();last_lap_sample=None
                driver = next((d for d in driver_info.get('Drivers',[]) if d.get('CarIdx')==driver_info.get('DriverCarIdx')), {})
                car_path = driver.get('CarPath','')
                try:
                    tires = TireMonitor(load_settings(car_path))
                except (ValueError, TypeError, OSError):
                    log('tire_config_invalid', car_path=car_path)
                    tires = TireMonitor()
                name = catalog.resolve(weekend.get('TrackName',''), weekend.get('TrackDisplayName',''), weekend.get('TrackConfigName',''))
                log('session', track=name, identifier=weekend.get('TrackName'), layout=weekend.get('TrackConfigName'), completed=completed)
                announcer.submit_opening(opening_sequence(name,ir['AirTemp'],ir['TrackTempCrew'],ir['TrackWetness']))
            report_sample=report_snapshot(ir)
            if time.monotonic()-driver_numbers_at>=2:
                write_snapshot(ir['DriverInfo'] or {},ir['CarIdxClassPosition'])
                driver_numbers_at=time.monotonic()
            finish_sample=review_snapshot(ir,name)
            finish_sample['flags']=int(ir['SessionFlags'] or 0)
            player_finished=finish_monitor.update(finish_sample)
            if finish_sample['mode']=='race':finish_sample['player_checkered']=player_finished
            for flag in flags_monitor.update(int(ir['SessionFlags'] or 0),time.monotonic(),player_finished=player_finished):
                log('flag_detected',text=flag,flags=int(ir['SessionFlags'] or 0))
                announcer.submit(flag)
            if time.monotonic()-flags_status_at>=2:
                mark_active(True);flags_status_at=time.monotonic()
            if report_sample['active']:
                fuel_history.update(completed,report_sample['fuel'],seconds,report_sample['pit'])
            else:
                fuel_history=FuelHistory()
            button_snapshot.update(status_phrase(report_sample,fuel_history) if report_sample['active'] else DISCONNECTED,report_sample['active'])
            damage_sample = sdk_snapshot(ir)
            for warning in damage.update(damage_sample, time.monotonic()):
                log('damage_detected', text=warning, evidence=damage_sample)
                announcer.submit(warning)
            tire_sample = tire_snapshot(ir)
            now = time.monotonic()
            for warning in tires.update(tire_sample, now):
                log('tire_detected', text=warning, evidence=tires.last_events)
                announcer.submit(warning)
            if now-tire_status_at>=2:
                tire_status_at = now
                tire_state = {'state':'connected','car_path':car_path,**tires.status()}
                write_json(ROOT/'tire_monitor_status.json',tire_state,ensure_ascii=False)
            sdk_lap=int(ir['Lap'] or 0)
            current_time=float(ir['LapCurrentLapTime'] or 0)
            lap_incidents.update(sdk_lap,incidents,completed,current_time,seconds)
            lap_sample=(completed,seconds,sdk_lap,incidents,finish_sample['state'],finish_sample['player_checkered'],finish_sample['score_lap'],finish_sample['score_seconds'])
            if lap_sample!=last_lap_sample:
                log('lap_sample',completed=completed,seconds=seconds,sdk_lap=sdk_lap,incidents=incidents,current_time=current_time,state=finish_sample['state'],player_finished=player_finished,player_flags_available=finish_sample['player_flags_available'],score_lap=finish_sample['score_lap'],score_seconds=finish_sample['score_seconds'],flags=finish_sample['flags'])
                last_lap_sample=lap_sample
            try:review_action('observe',dict(finish_sample,lap_valid=lap_incidents.valid(completed)),time.time())
            except (ValueError,TypeError,KeyError,AttributeError) as error:log('review_snapshot_error',detail=str(error)[:200])
            session_info=ir['SessionInfo'] or {}
            session_number=ir['SessionNum']
            kind=next((row.get('SessionType','') for row in session_info.get('Sessions',[]) if row.get('SessionNum')==session_number),'')
            # Official SDK SessionState: racing=4, checkered=5, cool_down=6.
            race_active=lap_reporting_allowed(kind,ir['SessionState'])
            if not race_active:
                tracker=LapTracker()
                tracker.update(completed,seconds,time.monotonic())
                message=None
            else:
                message = tracker.update(completed, seconds, time.monotonic(), valid=lap_incidents.valid(completed), current_lap=current_time,score_lap=finish_sample['score_lap'],score_seconds=finish_sample['score_seconds'])
            if message:
                log('lap_detected', lap=completed, sdk_lap=sdk_lap, incidents=incidents, invalid_laps=sorted(lap_incidents.invalid), seconds=seconds, text=message)
                announcer.submit(message)
            status.write_text(json.dumps({'state':'connected','completed_laps':completed,'last_lap_seconds':seconds,'track':name}, ensure_ascii=False), encoding='utf-8')
            review_action('tick',time.time())
            time.sleep(.25)
        except Exception as e:
            log('telemetry_error', error=type(e).__name__, detail=str(e)[:200])
            time.sleep(2)

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--self-test-audio', action='store_true')
    if parser.parse_args().self_test_audio:
        announcer = Announcer()
        announcer.submit('我是芙宁娜。本圈一分零九秒七。当前赛道是奥尔顿公园赛道，福斯特布局。')
        announcer.items.join()
    else:
        run()
