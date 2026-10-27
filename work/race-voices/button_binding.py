"""Learn a new controller button without consuming or injecting game input."""
import ctypes,json,time
from status_button import ROOT,WinMMButton,JoyInfo
from atomic_io import write_json

class Capture:
    def __init__(self):self.previous={}
    def update(self,current):
        pressed=[]
        for device,mask in current.items():
            if device in self.previous:
                rising=mask & ~self.previous[device]
                pressed.extend((device,bit+1) for bit in range(32) if rising&(1<<bit))
        self.previous=dict(current)
        return pressed[0] if len(pressed)==1 else None

def learn(timeout=30):
    flag=ROOT/'button_binding.active';cancel=ROOT/'button_binding.cancel'
    cancel.unlink(missing_ok=True)
    flag.write_text(str(time.time()),encoding='utf-8')
    readers={d:WinMMButton(d,1) for d in range(1,17)};capture=Capture()
    started=time.monotonic()
    print('等待按键：请先松开所有按钮，再按下要绑定的播报按钮。',flush=True)
    try:
        while time.monotonic()-started<timeout:
            if cancel.exists():print('已取消，原绑定保留。');return
            current={}
            for d,r in readers.items():
                info=JoyInfo();info.size=ctypes.sizeof(info);info.flags=0x80
                if r.api.joyGetPosEx(r.device,ctypes.byref(info))==0:current[d]=info.buttons
            selected=capture.update(current)
            if selected:
                device,button=selected;path=ROOT/'status_button_settings.json'
                cfg=json.loads(path.read_text(encoding='utf-8'))
                cfg.update(device=device,button=button)
                write_json(path,cfg,'.binding.tmp',ensure_ascii=False)
                print(f'绑定成功：控制器 {device}，按钮 {button}。已自动保存。',flush=True)
                # Suppress the learning press until release, then allow hot reload.
                until=time.monotonic()+3
                while time.monotonic()<until and WinMMButton(device,button).read():time.sleep(.05)
                return
            time.sleep(.02)
        print('等待超时，原绑定保留。')
    finally:flag.unlink(missing_ok=True);cancel.unlink(missing_ok=True)

if __name__=='__main__':learn()
