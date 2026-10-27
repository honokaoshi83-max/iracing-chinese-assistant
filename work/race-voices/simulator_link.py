"""Link voice-service lifetime to an optional, owned Simulator Controller."""
import json,subprocess,sys,time
from pathlib import Path
import psutil
from runtime_settings import project_root
from atomic_io import write_json

OWNER=Path(__file__).parent/'simulator_controller_owner.json'

def installation():
    sys.path.insert(0,str(project_root()/'app'))
    from simulator_status import inspect_simulator
    return inspect_simulator(project_root())

def running_voice_server():
    for process in psutil.process_iter(["name"]):
        if (process.info["name"] or "").casefold()=="voice server.exe":return process
    return None

def running_controller():
    for process in psutil.process_iter(['name']):
        if (process.info['name'] or '').casefold()=='simulator controller.exe':return process
    return None

def same_process(process,record):
    try:
        return process.pid==record['pid'] and Path(process.exe()).resolve()==Path(record['exe']).resolve() and abs(process.create_time()-record['created'])<.01
    except (psutil.Error,OSError,KeyError,TypeError):return False

def terminate(process):
    try:
        process.terminate();process.wait(timeout=3)
    except psutil.NoSuchProcess:pass
    except psutil.TimeoutExpired:process.kill();process.wait(timeout=3)

class SimulatorLink:
    def __init__(self,owner=OWNER,inspect=installation):
        self.owner=Path(owner);self.inspect=inspect;self.next_attempt=0

    def record(self):
        try:return json.loads(self.owner.read_text(encoding='utf-8'))
        except (OSError,ValueError):return {}

    def ensure(self,voice_ready):
        if not voice_ready:return 'waiting_for_voice'
        state=self.inspect()
        if not state['installed']:return 'not_installed'
        if not state['configured'] or not state['integrated']:return 'needs_configuration'
        running=running_controller()
        if running:
            if not same_process(running,self.record()):return 'running_manual'
            if running_voice_server() and self.record().get('voice_pid')==running_voice_server().pid:return 'running_owned'
            # Migrate our own controller from independent speakers to the broker.
            self.stop()
        if time.monotonic()<self.next_attempt:return 'waiting_to_retry'
        if state.get('documents'):
            from simulator_identity import ensure_identity
            from slow_car_settings import ensure_slow_car_limit
            home=state['documents']/'Simulator Controller'
            ensure_identity(home)
            if not ensure_slow_car_limit(home):
                return 'needs_database_identity'
        self.next_attempt=time.monotonic()+30
        executable=state['root']/'Binaries/Simulator Controller.exe'
        broker_exe=executable.parent/'Voice Server.exe'
        if not broker_exe.is_file():return 'voice_server_missing'
        startup=None
        if sys.platform=='win32':
            startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
        def launch(exe,args=()):
            child=subprocess.Popen([str(exe),*args],cwd=exe.parent,startupinfo=startup,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            process=psutil.Process(child.pid)
            return process,dict(pid=child.pid,exe=str(exe.resolve()),created=process.create_time())
        broker=running_voice_server()
        previous_broker=self.record().get("voice_server",{})
        broker_record=previous_broker if broker and same_process(broker,previous_broker) else {}
        if broker is None:
            broker,broker_record=launch(broker_exe)
            # Allow the broker to register its IPC handlers before assistants start.
            time.sleep(1)
            if not broker.is_running():return 'voice_server_failed'
        process=None
        try:
            process,record=launch(executable,['-Voice',str(broker.pid)])
            record['voice_pid']=broker.pid
            if broker_record:record['voice_server']=broker_record
            self.owner.parent.mkdir(parents=True,exist_ok=True)
            write_json(self.owner,record)
        except Exception:
            if process:terminate(process)
            if broker_record:terminate(broker)
            raise
        return 'started'

    def stop(self):
        record=self.record()
        if not record:return 'not_owned'
        stopped=False
        try:
            try:
                process=psutil.Process(record['pid'])
                if same_process(process,record):
                    binaries=Path(record['exe']).resolve().parent
                    for child in process.children(recursive=True):
                        try:
                            if Path(child.exe()).resolve().is_relative_to(binaries):terminate(child)
                        except psutil.NoSuchProcess:pass
                    terminate(process);stopped=True
            except psutil.NoSuchProcess:pass
            broker_record=record.get('voice_server',{})
            if broker_record:
                try:
                    broker=psutil.Process(broker_record['pid'])
                    if same_process(broker,broker_record):terminate(broker);stopped=True
                except psutil.NoSuchProcess:pass
            self.owner.unlink(missing_ok=True)
            return 'stopped' if stopped else 'not_owned'
        except (psutil.Error,OSError,KeyError,TypeError):return 'stop_failed'
