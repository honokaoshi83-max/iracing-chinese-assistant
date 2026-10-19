"""Persist native race reviews and deterministic Time Trial reviews."""
import json,math,statistics,time,uuid
from datetime import datetime,timezone
from pathlib import Path
from review_store import save_review,atomic
from review_phrases import SAVED

class ReviewLine(str):
    voice='furina'
    review=True

def stamp(now):return datetime.fromtimestamp(now,timezone.utc).astimezone().isoformat(timespec='seconds')

def remaining_seconds(value):
    # Reject unlimited sentinels and corrupt/unknown telemetry, never guess a timer.
    return value if type(value) in (int,float) and math.isfinite(value) and 0<=value<=86400 else None

def session_mode(event_type,session_type):
    event=''.join(c for c in str(event_type).casefold() if c.isalnum())
    session=''.join(c for c in str(session_type).casefold() if c.isalnum())
    if event=='timetrial' or session=='timetrial':return 'time_trial'
    return 'race' if session=='race' else None

def time_trial_summary(laps):
    rows=[r for r in laps if isinstance(r.get('seconds'),(int,float)) and math.isfinite(r['seconds']) and 10<=r['seconds']<=3600]
    if len(laps)<2:raise ValueError('至少需要两个已记录完成圈')
    times=[r['seconds'] for r in rows];valid=[r['seconds'] for r in rows if r['valid']]
    invalid=sum(not r['valid'] for r in laps);average=statistics.mean(times) if times else None;sd=statistics.pstdev(times) if times else None
    from review_consistency import measure,description,pace_trend
    consistency=measure(laps)
    from telemetry_announcer import lap_phrase,chinese_number
    summary=[f'本次计时赛记录{chinese_number(len(laps))}个完成圈，其中{chinese_number(len(valid))}个有效圈，{chinese_number(invalid)}个犯规无效圈。']
    if valid:
        summary.append('最快有效圈'+lap_phrase(min(valid))[2:])
        summary.append('有效平均圈速'+lap_phrase(statistics.mean(valid))[2:])
    else:summary.append('本次没有有效圈速，先完成连续有效圈。')
    trend,delta=pace_trend(valid)
    if trend!='insufficient':
        if trend!='steady':
            from speech_reading import normalize_reading
            summary.append(normalize_reading(f"后半段有效圈平均比前半段{'快' if delta<0 else '慢'}{abs(delta):.1f}秒。"))
        else:summary.append('前后半段的平均有效圈速接近。')
    summary.extend(description(consistency))
    if invalid:summary.append('有用时的无效圈计入全部计时圈波动率，犯规比例单独评价。')
    return dict(observed_laps=len(laps),timed_laps=len(rows),valid_laps=len(valid),invalid_laps=invalid,best_valid=min(valid) if valid else None,
                valid_average=statistics.mean(valid) if valid else None,
                summary=summary,**consistency)

class ReviewCoordinator:
    def __init__(self,project,emit):
        self.project=Path(project);self.root=self.project/'work/race-voices';self.emit=emit
        self.path=self.root/'review_state.json';self.current=None;self.pending=[];self.done=[];self.offset=0;self.last_flush=0
        self.error=None;self.status_error=None
        try:
            data=json.loads(self.path.read_text(encoding='utf-8-sig'))
            if isinstance(data,dict) and data.get('schema')==1 and (data.get('current') is None or isinstance(data.get('current'),dict)) and isinstance(data.get('pending',[]),list):
                self.current=data.get('current');self.pending=data.get('pending',[]);self.done=data.get('done',[]);self.offset=data.get('offset',0)
        except (OSError,ValueError,TypeError):pass

    def persist(self,now=None):
        atomic(self.path,json.dumps(dict(schema=1,current=self.current,pending=self.pending,done=self.done[-500:],offset=self.offset),ensure_ascii=False,allow_nan=False))
        self.last_flush=time.time() if now is None else now

    def records(self):return self.pending+([self.current] if self.current else [])

    def observe(self,sample,now):
        if 'time_remaining' in sample:sample=dict(sample,time_remaining=remaining_seconds(sample['time_remaining']))
        mode=sample.get('mode')
        if sample.get('replay'):return
        old=self.current
        reset=old and sample.get('clock',0)+1<old.get('last_clock',0)
        if old and (old['key']!=sample['key'] or reset):
            self.end('切换会话',now);self.pending.append(old);self.current=None
        if mode not in ('race','time_trial'):
            return
        if self.current is None:
            waiting=next((r for r in reversed(self.pending) if r['key']==sample['key'] and not r.get('saved') and sample.get('clock',0)>=r.get('last_clock',0)),None)
            if waiting is not None:
                self.pending.remove(waiting);self.current=waiting;old=waiting
        if old and self.current is old and old.get('reason') in ('退出会话或连接中断','切换会话') and sample.get('state') in (4,5,6):
            # A saved disconnect report is a snapshot, not proof that this race ended.
            # Keep that file and create a successor if the same session resumes.
            gap=max(0,sample.get('clock',old['last_clock'])-old['last_clock'])
            old['unobserved_seconds']=old.get('unobserved_seconds',0)+gap
            old['coverage_gaps']=True
            if old.get('saved'):
                old['previous_review_id']=old['id'];old['id']=uuid.uuid4().hex
            for key in ('saved','ended_wall','end_clock','reason','race_started_at','start_clock','score_tracker','retry_after','last_native','terminal'):
                old.pop(key,None)
            old.update(ended_at=None,native_started=False,native=[],inflight={},seen_requests=[])
        if self.current is None:
            self.current=dict(sample,id=uuid.uuid4().hex,started_at=stamp(now),first_clock=sample.get('clock',0),
                last_clock=sample.get('clock',0),last_seen=now,laps=[],native=[],inflight={},seen_requests=[],pit_laps=[],ended_at=None)
        row=self.current;previous=row.get('state');old_timer=row.get('current_time');row.pop('disconnected_wall',None);row.pop('simulator_running',None);row.update({k:v for k,v in sample.items() if v is not None})
        row['last_clock']=sample.get('clock',row['last_clock']);row['last_seen']=now
        remaining=remaining_seconds(sample.get('time_remaining'))
        if remaining is not None and sample.get('state') in (4,5):row['expected_end_wall']=now+remaining
        if sample.get('pit') and sample.get('sdk_lap') not in row['pit_laps']:row['pit_laps'].append(sample.get('sdk_lap'))
        timer=sample.get('current_time')
        if sample.get('completed')==0 and sample.get('last_seconds',0)<=0 and isinstance(old_timer,(int,float)) and isinstance(timer,(int,float)) and old_timer>1 and timer+1<old_timer:
            row['pit_laps']=[n for n in row['pit_laps'] if n!=sample.get('sdk_lap')]
        if previous in (1,2,3) and sample.get('state')==4 and not row.get('race_started_at'):
            row['race_started_at']=stamp(now);row['start_clock']=row['last_clock']
        if sample.get('disqualified'):row['disqualified']=True
        self.sample_lap(sample,now)
        # Global race checkered does not prove this player has finished.
        finished=sample.get('state')==6 or (sample.get('state') in (5,6) and sample.get('player_checkered'))
        if finished:
            row['end_confirmed']=True
            if not row.get('ended_at'):self.end('会话结束',now)
            elif row.get('reason') in ('退出会话或连接中断','切换会话','策略师触发赛后复盘'):
                row.update(ended_at=stamp(now),ended_wall=now,reason='会话结束',end_clock=row['last_clock'])
        if now-self.last_flush>=1:self.persist(now)

    def sample_lap(self,sample,now):
        if 'last_seconds' not in sample:return
        row=self.current;number=sample.get('completed',0);seconds=sample['last_seconds']
        tracker=row.setdefault('score_tracker',dict(completed=number,seconds=seconds,pending=None))
        if number<tracker['completed']:
            tracker.update(completed=number,seconds=seconds,pending=None);return
        if number>tracker['completed']:
            tracker['pending']=dict(lap=number,time=now,valid=sample.get('lap_valid',False)) if number==tracker['completed']+1 else None
            tracker['completed']=number
        pending=tracker['pending']
        if not pending:return
        pending['valid']=pending['valid'] and sample.get('lap_valid',False)
        if now-pending['time']>5:
            if not pending['valid']:self.add_lap(number,None,False)
            tracker.update(seconds=seconds,pending=None);return
        if now-pending['time']<.3:return
        # Result publication ties identical times to the correct completed lap.
        published=sample.get('score_lap')==number and isinstance(sample.get('score_seconds'),(int,float)) and 10<=sample['score_seconds']<=3600
        score=sample['score_seconds'] if published else seconds
        if isinstance(score,(int,float)) and math.isfinite(score) and 10<=score<=3600 and (published or score!=tracker['seconds']):
            self.add_lap(number,score,pending['valid']);tracker.update(seconds=score,pending=None)

    def add_lap(self,number,seconds,valid,pit=False):
        row=self.current
        if not row or row.get('saved') or number<=0:return
        if seconds is None:
            if valid:return
        elif not isinstance(seconds,(int,float)) or not math.isfinite(seconds) or not 10<=seconds<=3600:return
        if row.get('ended_wall') is not None and row['last_seen']-row['ended_wall']>5:return
        if pit or number in row['pit_laps']:return
        existing=next((r for r in row['laps'] if r['lap']==number),None)
        if existing:
            if existing['seconds'] is None and seconds is not None:existing['seconds']=seconds;self.persist()
            return
        row['laps'].append(dict(lap=number,seconds=seconds,valid=bool(valid)));self.persist()

    def end(self,reason,now):
        row=self.current
        if not row or row.get('ended_at'):return
        row.update(ended_at=stamp(now),ended_wall=now,reason=reason,end_clock=row['last_clock'])
        pending=row.get('score_tracker',{}).get('pending')
        if pending and not pending['valid']:self.add_lap(pending['lap'],None,False)
        self.persist(now)

    def disconnect(self,now,simulator_running=False):
        if self.current and not self.current.get('ended_at'):
            changed='disconnected_wall' not in self.current or self.current.get('simulator_running')!=simulator_running
            self.current.setdefault('disconnected_wall',now);self.current['simulator_running']=simulator_running
            if changed:self.persist(now)

    def native(self,event,now):
        kind=event.get('kind');request_id=event.get('id')
        if kind=='save':return
        row=next((r for r in self.records() if request_id in r.get('inflight',{})),None)
        if event.get('event')=='request':
            if kind=='start':
                candidates=[r for r in self.records() if r['mode']=='race' and not r.get('saved') and not r.get('disqualified') and now-r.get('ended_wall',now)<180]
                # Prefer a recently ended race over the newly loaded session.
                row=next((r for r in reversed(candidates) if r.get('ended_at')),candidates[-1] if candidates else None)
            else:
                row=next((r for r in reversed(self.records()) if r.get('native_started') and not r.get('saved')),None)
            if not row or request_id in row['seen_requests']:return
            if not row.get('ended_at'):
                premature=row.get('state') in (4,5) and not row.get('end_confirmed')
                if row is self.current and not premature:self.end('策略师触发赛后复盘',now)
            row['native_started']=True;row['seen_requests'].append(request_id);row['inflight'][request_id]=now
            if event.get('text') and event['text'] not in row['native']:row['native'].append(event['text'])
            if kind=='end' or (kind=='start' and event.get('values',{}).get('position') in ('1','一')):row['terminal']=True
        elif row:
            row['inflight'].pop(request_id,None)
            if event.get('event')=='failed':row['incomplete']=True
        else:return
        row['last_native']=now;self.persist(now)

    def read_events(self,now):
        path=self.root/'review_native_events.jsonl'
        if not path.exists():return
        if path.stat().st_size<self.offset:self.offset=0
        with path.open('rb') as stream:
            stream.seek(self.offset);data=stream.read(65536)
        end=data.rfind(b'\n')
        if end<0:return
        for line in data[:end].splitlines():
            try:
                event=json.loads(line);self.native(event,event.get('time',now))
            except (ValueError,TypeError,KeyError):continue
        self.offset+=end+1;self.persist(now)

    def tick(self,now):
        self.read_events(now)
        deadline=self.current.get('expected_end_wall') if self.current else None
        countdown_elapsed=deadline is not None and now>=deadline
        if self.current and (not self.current.get('simulator_running',True) or countdown_elapsed) and now-self.current.get('disconnected_wall',now)>=20:
            self.end('退出会话或连接中断',self.current.get('last_seen',now))
        for row in self.records():
            if row.get('saved') or not row.get('ended_at') or now<row.get('retry_after',0):continue
            left_early=row.get('reason') in ('退出会话或连接中断','切换会话','策略师触发赛后复盘') and not row.get('official') and not row.get('end_confirmed')
            deadline=row.get('expected_end_wall') if left_early else None
            if deadline is not None and now<deadline:continue
            if row['mode']=='race':
                if not row.get('native_started') and now-row['ended_wall']<30:continue
                for key,started in list(row['inflight'].items()):
                    if now-started>180:row['inflight'].pop(key);row['incomplete']=True
                if row['inflight'] or (row.get('native_started') and now-row.get('last_native',now)<(2 if row.get('terminal') else 20)):continue
                from review_assessment import assess_session
                stats=assess_session(row);summary=stats['summary']
            else:
                if now-row['ended_wall']<6:continue
                if len(row['laps'])<2:continue
                from review_assessment import assess_session
                stats=assess_session(row);summary=stats['summary']
            report=dict(row,saved_at=stamp(now),summary=summary,stats=stats,
                        observed_seconds=max(0,row.get('end_clock',row['last_clock'])-row['first_clock']-row.get('unobserved_seconds',0)),
                        total_seconds=max(0,row.get('end_clock',row['last_clock'])-row['start_clock']) if row.get('race_started_at') and not left_early else None,
                        left_early=left_early,expected_end_at=stamp(deadline) if deadline is not None else None)
            if left_early:
                note='玩家在赛事结束前退出，评语仅依据退出前已记录的数据。'
                summary.append(note)
                if deadline is not None:summary.append('复盘按退出前的赛事剩余时间，等待预计结束后生成；实际结束状态未确认。')
                if row['mode']=='race':report['position_source']='SDK退出前最后观测值，最终结果未确认'
            try:
                result=save_review(self.project,report);row['saved']=True;self.done.append(row['id']);self.error=None;self.persist(now)
                if result['new']:
                    self.emit(ReviewLine(SAVED))
            except (OSError,ValueError) as error:
                row['retry_after']=now+5;self.error='复盘保存失败：'+str(error);self.persist(now)
        self.pending=[r for r in self.pending if not r.get('saved') and now-max(r.get('ended_wall',now),r.get('expected_end_wall',0))<600]
        if self.error!=self.status_error:
            atomic(self.root/'review_status.json',json.dumps({'error':self.error},ensure_ascii=False));self.status_error=self.error

def simulator_is_running():
    import psutil
    try:return any((p.info.get('name') or '').lower().startswith('iracingsim') for p in psutil.process_iter(['name']))
    except psutil.Error:return True # An unreadable process list is not proof of exit.

def snapshot(ir,track_name):
    def get(name,default=None):
        try:return ir[name] if ir[name] is not None else default
        except (KeyError,TypeError,RuntimeError):return default
    weekend=get('WeekendInfo',{});info=get('SessionInfo',{});num=get('SessionNum',0)
    session=next((r for r in (info.get('Sessions') or []) if r.get('SessionNum')==num),{})
    drivers=get('DriverInfo',{});idx=drivers.get('DriverCarIdx');people=drivers.get('Drivers') or []
    driver=next((r for r in people if r.get('CarIdx')==idx),{})
    def positive(value):return value if isinstance(value,int) and value>0 else None
    result=next((r for r in (session.get('ResultsPositions') or []) if r.get('CarIdx')==idx),{})
    pos=positive(get('PlayerCarPosition'));cls=positive(get('PlayerCarClassPosition'))
    final=bool(session.get('ResultsOfficial'))
    if final:
        pos=positive(result.get('Position')) or pos
        cls=result.get('ClassPosition',-1)+1 if isinstance(result.get('ClassPosition'),int) else cls
    qualifying=get('QualifyResultsInfo',{})
    grid=next((r.get('Position',-1)+1 for r in (qualifying.get('Results') or []) if r.get('CarIdx')==idx),None)
    flags=get('CarIdxSessionFlags',[]);player_flags=flags[idx] if isinstance(idx,int) and 0<=idx<len(flags) else None
    mode=session_mode(weekend.get('EventType',''),session.get('SessionType',''))
    return dict(key=str((weekend.get('SubSessionID'),weekend.get('SessionID'),weekend.get('TrackID'),num)),mode=mode,
        track=track_name,layout=weekend.get('TrackConfigName',''),car=driver.get('CarScreenName','未知'),
        state=get('SessionState',0),clock=get('SessionTime',0),time_remaining=remaining_seconds(get('SessionTimeRemain')),completed=get('LapCompleted',0),sdk_lap=get('Lap',0),
        last_seconds=get('LapLastLapTime',0),score_lap=result.get('LapsComplete'),score_seconds=result.get('LastTime'),
        current_time=get('LapCurrentLapTime',0),
        pit=bool(get('OnPitRoad',False)),replay=bool(get('IsReplayPlaying',False)),position=pos,class_position=cls,
        grid=positive(grid),cars=len([r for r in people if not r.get('CarIsPaceCar') and not r.get('IsSpectator')]) or None,
        player_checkered=bool((player_flags if player_flags is not None else get('SessionFlags',0) if mode=='time_trial' else 0)&1),
        player_flags_available=player_flags is not None,
        disqualified=bool(get('SessionFlags',0)&0x20000),official=final,
        position_source='SDK正式结果' if final and positive(result.get('Position')) else 'SDK最后观测值，最终结果未确认')
