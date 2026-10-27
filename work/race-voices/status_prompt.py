"""Immediate, local acknowledgement for accepted status-button requests."""
import array,io,time,wave,threading
from pathlib import Path
from output_volume import load_volume
from runtime_settings import project_root
from atomic_io import write_bytes

ACK_TEXT='正在整理具体数据，即将开始播报。'

def prompt_path():return project_root()/'data/audio/status-ack.wav'

def save_prompt(data,path=None):
    # Store normalized audio independent of the current output gain.
    from bridge import normalize_audio
    from wav_playback import finalize_wav
    target=Path(path or prompt_path());target.parent.mkdir(parents=True,exist_ok=True)
    write_bytes(target,finalize_wav(normalize_audio(data,'wav')))

class LocalStatusPrompt:
    def __init__(self,path,speaking,root,player=None):
        self.path=Path(path);self.speaking=Path(speaking);self.root=Path(root);self.player=player
        self.audio=None;self.duration=0;self.params=None;self.levels={}
        try:
            with wave.open(str(self.path),'rb') as w:
                self.params=w.getparams();self.audio=w.readframes(w.getnframes())
                if w.getsampwidth()!=2 or not self.audio:raise ValueError('Invalid prompt PCM')
                self.duration=len(self.audio)/(w.getnchannels()*w.getsampwidth()*w.getframerate())
        except (OSError,ValueError,wave.Error):self.audio=None

    def monitor(self,until,force=False):
        import winsound
        from button_priority import active_spotter_safety
        while time.monotonic()<until:
            if self.speaking.exists() and (not force or active_spotter_safety()):
                (self.player or winsound.PlaySound)(None,winsound.SND_PURGE)
                return
            time.sleep(.03)

    def play(self,force=False):
        from button_priority import active_spotter_safety
        if self.audio is None or (self.speaking.exists() and (not force or active_spotter_safety())):return 0
        volume=load_volume(self.root)
        if volume==0:return 0
        try:
            path=self.path
            if volume!=100:
                if volume not in self.levels:
                    samples=array.array('h',self.audio)
                    samples=array.array('h',(int(sample*volume/100) for sample in samples))
                    folder=self.root/'cache/button-prompts';folder.mkdir(parents=True,exist_ok=True)
                    path=folder/f'ack-{volume:g}.wav'
                    with wave.open(str(path),'wb') as w:w.setparams(self.params);w.writeframes(samples.tobytes())
                    self.levels[volume]=path
                path=self.levels[volume]
            import winsound
            (self.player or winsound.PlaySound)(str(path),winsound.SND_FILENAME|winsound.SND_ASYNC|winsound.SND_NODEFAULT)
            until=time.monotonic()+self.duration
            if self.player is None:
                threading.Thread(target=self.monitor,args=(until,force),daemon=True).start()
            return until
        except (OSError,RuntimeError,ValueError):return 0
