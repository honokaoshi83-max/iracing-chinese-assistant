"""Announce flag transitions from SDK flags, never infer a penalty offence."""
import json,time,re
from pathlib import Path
from atomic_io import write_json

STATUS=Path(__file__).resolve().parent/'flag_monitor_status.json'
RULES=[('dq',0x20000|0x200000,'黑旗，已被取消资格，请安全离开赛道。'),
       ('repair',0x100000,'橙圆旗，赛车存在需要维修的机械问题，请安全返回维修区。'),
       ('black',0x10000,'黑旗，收到处罚，请查看屏幕上的处罚原因和要求。'),
       ('red',0x10,'红旗，会话暂停，请减速并遵守赛会指示。'),
       ('caution',0x4000|0x8000,'全场黄旗，全场进入警戒状态，减速并遵守赛会指示。'),
       ('yellow',0x8|0x100,'黄旗，前方有危险，减速并留意路况。'),
       ('blue',0x20,'蓝旗，后方有更快赛车，保持稳定路线并留意让车。'),
       ('debris',0x40,'油旗，路面附着力可能降低，留意湿滑或碎片。'),
       ('white',0x2,'白旗，进入最后一圈。'),
       ('checkered',0x1,'方格旗，本次比赛结束，请安全返回。'),
       ('green',0x4,'绿旗，赛道开放，可以正常行驶。')]

class FlagLine(str):
    voice='furina'
    flag=True

class PlayerFinish:
    """A leader's checkered starts the finishing phase, not everyone's finish."""
    def __init__(self):self.finished=False;self.checkered=False;self.completed=None

    def update(self,sample):
        if sample.get('mode')!='race':return True
        state=sample.get('state');completed=sample.get('completed',0)
        finishing=state in (5,6) or bool(sample.get('flags',0)&1)
        crossed=self.completed is not None and completed>self.completed
        if state==6:self.finished=True
        elif sample.get('player_flags_available'):
            self.finished |= finishing and bool(sample.get('player_checkered'))
        elif finishing and crossed and (self.checkered or sample.get('position')==1):
            self.finished=True
        self.checkered=finishing;self.completed=completed
        return self.finished

class FlagMonitor:
    def __init__(self):self.previous=set();self.seen=False;self.last={}
    def update(self,flags,now,player_finished=True):
        if not player_finished:flags &= ~0x1
        active={name for name,mask,_ in RULES if flags & mask}
        if 'dq' in active:active.discard('black')
        if 'caution' in active:active.discard('yellow')
        if 'checkered' in active or 'red' in active:active.discard('green')
        lines=[]
        for name,_,text in RULES:
            if name in active-self.previous and (self.seen or name!='green'):
                if now-self.last.get(name,-1000)>=15:
                    lines.append(FlagLine(text));self.last[name]=now
        if 'yellow' in self.previous and not active & {'yellow','caution'}:
            lines.append(FlagLine('黄旗解除，危险提示已取消。'))
        self.previous=active;self.seen=True
        return lines

def native_flag(text):return bool(re.search(r'黄旗|蓝旗|黑旗|红旗|绿旗|白旗|方格旗|橙圆旗|油旗',text))
def managed():
    try:return json.loads(STATUS.read_text(encoding='utf-8')).get('until',0)>time.time()
    except (OSError,ValueError):return False
def mark_active(active):
    if not active:STATUS.unlink(missing_ok=True);return
    write_json(STATUS,{'until':time.time()+6})
