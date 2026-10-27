"""Read the existing 1Joy25 binding without consuming any game input."""
import ctypes
import json
import math
import os
import queue
import threading
import time
from pathlib import Path
from atomic_io import append_jsonl,write_json

ROOT=Path(__file__).resolve().parent

class PressLatch:
    def __init__(self,cooldown=8):
        self.previous=None; self.last=-float('inf'); self.cooldown=cooldown

    def update(self,down,now):
        if down is None:
            self.previous=None
            return False
        rising=self.previous is False and down is True
        self.previous=down
        if rising and now-self.last>=self.cooldown:
            self.last=now
            return True
        return False

class JoyInfo(ctypes.Structure):
    _fields_=[(name,ctypes.c_uint32) for name in ('size','flags','x','y','z','r','u','v','buttons','button_number','pov','reserved1','reserved2')]

class JoyCaps(ctypes.Structure):
    _fields_=[('mid',ctypes.c_uint16),('pid',ctypes.c_uint16),('name',ctypes.c_wchar*32)]+[(n,ctypes.c_uint32) for n in ('xmin','xmax','ymin','ymax','zmin','zmax','buttons','period_min','period_max','rmin','rmax','umin','umax','vmin','vmax','caps','max_axes','axes','max_buttons')]+[('registry_key',ctypes.c_wchar*32),('oem',ctypes.c_wchar*260)]

class WinMMButton:
    def __init__(self,device=1,button=25):
        if not 1<=device<=16 or not 1<=button<=32:
            raise ValueError('Invalid WinMM joystick binding')
        self.device=device-1; self.button=button
        self.api=ctypes.WinDLL('winmm')
        self.api.joyGetPosEx.argtypes=[ctypes.c_uint,ctypes.POINTER(JoyInfo)]
        self.api.joyGetPosEx.restype=ctypes.c_uint
        self.api.joyGetDevCapsW.argtypes=[ctypes.c_size_t,ctypes.POINTER(JoyCaps),ctypes.c_uint]
        self.api.joyGetDevCapsW.restype=ctypes.c_uint

    def read(self):
        info=JoyInfo(); info.size=ctypes.sizeof(info); info.flags=0x80
        if self.api.joyGetPosEx(self.device,ctypes.byref(info))!=0:
            return None
        return bool(info.buttons & (1<<(self.button-1)))

    def describe(self):
        caps=JoyCaps()
        ok=self.api.joyGetDevCapsW(self.device,ctypes.byref(caps),ctypes.sizeof(caps))==0
        return dict(binding=f'{self.device+1}Joy{self.button}',device_name=caps.name if ok else None,
                    button_count=caps.buttons if ok else None,connected=self.read() is not None)

def configured_reader(cfg):
    device,button=cfg.get('device'),cfg.get('button')
    if type(device) is not int or type(button) is not int or not 1<=device<=16 or not 1<=button<=32:return None
    return WinMMButton(device,button)

class StatusButton:
    def __init__(self,on_event=None,start=True,root=None,reader_factory=None):
        self.root=Path(root or ROOT);self.on_event=on_event;self.factory=reader_factory or configured_reader
        self.reader=None;self.latch=PressLatch(8);self.stamp=None;self.cfg={}
        self.last_error=None;self.last_press=None;self.last_consumed=None;self.last_status=0;self.last_reopen=0
        self.events=queue.Queue(maxsize=1)
        self.reload()
        if start:threading.Thread(target=self.watch,name='protected-controller-watch',daemon=True).start()

    def diagnostic(self,event,**data):
        try:
            callback=getattr(self,'on_event',None)
            if callback:callback(event,**data)
            else:
                root=getattr(self,'root',ROOT)
                append_jsonl(root/'button_events.jsonl',dict(time=time.time(),event=event,**data))
        except Exception:pass # Diagnostics must never disable the button.

    def fault(self,error):
        detail=type(error).__name__+': '+str(error)[:150]
        if detail!=self.last_error:self.diagnostic('button_error',detail=detail)
        self.last_error=detail

    def reload(self):
        path=self.root/'status_button_settings.json'
        try:
            before=path.stat().st_mtime_ns
            if before==self.stamp:return
            cfg=json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(cfg,dict):raise ValueError('Invalid button configuration')
            if path.stat().st_mtime_ns!=before:return
            cooldown=cfg.get('cooldown_seconds',8)
            if type(cooldown) not in (int,float) or not math.isfinite(cooldown) or cooldown<0:raise ValueError('Invalid cooldown')
            reader=self.factory(cfg)
            self.reader=reader;self.cfg=cfg;self.latch=PressLatch(cooldown);self.stamp=before
            self.last_error=None;self.diagnostic('button_binding_loaded',device=cfg.get('device'),button=cfg.get('button'))
        except (OSError,ValueError,TypeError) as error:self.fault(error)

    def poll(self,now):
        self.reload();flag=self.root/'button_binding.active'
        if flag.exists() and time.time()-flag.stat().st_mtime<40:
            self.latch.previous=None
            while not self.events.empty():
                try:self.events.get_nowait()
                except queue.Empty:break
            return
        down=self.reader.read() if self.reader else None
        if self.latch.update(down,now):
            self.last_press=time.time();self.diagnostic('button_captured')
            try:self.events.put_nowait(now);self.diagnostic('button_enqueued')
            except queue.Full:self.diagnostic('button_already_pending')
        if down is None and self.cfg and now-self.last_reopen>=2:
            self.last_reopen=now;self.reader=self.factory(self.cfg)
        if now-self.last_status>=2:
            detail=self.reader.describe() if self.reader else dict(binding=None,device_name=None,button_count=None,connected=False)
            state={**detail,'mode':'single_press' if self.reader else 'unbound','cooldown_seconds':self.latch.cooldown,
                'time':time.time(),'button_down':down,'last_press':self.last_press,'last_consumed':self.last_consumed,
                'pending':self.events.qsize(),'last_error':self.last_error,'pid':os.getpid()}
            try:write_json(self.root/'status_button_status.json',state,ensure_ascii=False)
            except OSError:pass
            self.last_status=now

    def watch(self):
        while True:
            try:self.poll(time.monotonic())
            except Exception as error:
                self.fault(error);self.latch.update(None,time.monotonic())
                try:self.reader=self.factory(self.cfg)
                except Exception:pass
            time.sleep(.02)

    def requested(self):
        try:
            created=self.events.get_nowait()
            self.last_consumed=time.time();self.diagnostic('button_consumed',age_seconds=round(time.monotonic()-created,3))
            return True
        except queue.Empty:
            return False
