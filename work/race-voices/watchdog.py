"""Silent, single-instance supervisor for the owned local race services."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil
import requests
from app_lifecycle import owner_alive

ROOT = Path(__file__).resolve().parent
from runtime_settings import service_url, settings, instance_id
STOP = ROOT/'watchdog.stop'
OWNER_TOKEN = None

class RecoveryPolicy:
    def __init__(self):
        self.failures = self.healthy = self.attempts = 0
        self.next_attempt = 0

    def due(self, healthy, now):
        if healthy:
            self.failures = 0
            self.healthy += 1
            if self.healthy >= 3:
                self.attempts = 0
                self.next_attempt = 0
            return False
        self.healthy = 0
        self.failures += 1
        return self.failures >= 3 and now >= self.next_attempt

    def attempted(self, now):
        self.attempts += 1
        self.next_attempt = now + min(60, 5 * 2 ** min(self.attempts-1, 4))

def owns_command(command, script):
    if len(command) < 2 or '-c' in command or '-m' in command:
        return False
    return any(not arg.startswith('-') and Path(arg).resolve() == script.resolve()
               for arg in command[1:])

def processes(script):
    found = []
    for p in psutil.process_iter(['cmdline','name']):
        try:
            if 'python' in (p.info['name'] or '').lower() and owns_command(p.info['cmdline'] or [], ROOT/script):
                found.append(p)
        except (psutil.Error, OSError):
            pass
    return found

def log(event, **data):
    with (ROOT/'watchdog_events.jsonl').open('a',encoding='utf-8') as f:
        f.write(json.dumps(dict(time=time.strftime('%Y-%m-%d %H:%M:%S'),event=event,**data))+'\n')

def launch(script, prefix, extra=()):
    if STOP.exists() or not owner_alive(ROOT, OWNER_TOKEN):
        return
    with (ROOT/(prefix+'.log')).open('a',encoding='utf-8') as out, (ROOT/(prefix+'-error.log')).open('a',encoding='utf-8') as err:
        p = subprocess.Popen([sys.executable,'-X','utf8',str(ROOT/script),*extra], cwd=ROOT,
                             stdout=out,stderr=err,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    (ROOT/(prefix+'.pid')).write_text(str(p.pid))
    log('launched',script=script,pid=p.pid)

def health():
    try:
        r=requests.get(service_url()+'/health',timeout=1)
        r.raise_for_status()
        data=r.json()
        return data.get('status')=='ready' and {'furina','clorinde'}.issubset(data.get('voices',[])) and data.get('device')=='cpu' and data.get('instance')==instance_id()
    except (requests.RequestException, ValueError):
        return False

def port_occupied():
    return any(c.laddr and c.laddr.port==settings()['port'] and c.status=='LISTEN'
               for c in psutil.net_connections(kind='tcp'))


def stop_children():
    """Only this deployment's parsed script paths; never a foreign Python."""
    from simulator_link import SimulatorLink
    log('simulator_controller_stop', result=SimulatorLink().stop())
    for script in ('telemetry_announcer.py', 'bridge.py'):
        for process in processes(script):
            try:
                process.terminate(); process.wait(timeout=5)
            except psutil.NoSuchProcess:
                pass
            except psutil.TimeoutExpired:
                process.kill(); process.wait(timeout=3)

def main(owner_token):
    global OWNER_TOKEN
    OWNER_TOKEN = owner_token
    import msvcrt
    # The OS releases this lock if the supervisor exits unexpectedly.
    with (ROOT/'watchdog.lock').open('a+b') as lock:
        lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            return
        (ROOT/'watchdog.pid').write_text(str(os.getpid()))
        log('started',pid=os.getpid())
        policies={name:RecoveryPolicy() for name in ('bridge.py','telemetry_announcer.py')}
        previous=None
        from simulator_link import SimulatorLink
        simulator=SimulatorLink()
        while not STOP.exists() and owner_alive(ROOT, OWNER_TOKEN):
            try:
                state={}
                for script,policy in policies.items():
                    owned=processes(script)
                    healthy=bool(owned) and (health() if script=='bridge.py' else True)
                    state[script]='healthy' if healthy else 'unavailable'
                    if script=='bridge.py' and not owned and port_occupied():
                        state[script]='port_conflict'
                        policy.due(False,time.monotonic())
                        continue
                    if policy.due(healthy,time.monotonic()):
                        policy.attempted(time.monotonic())
                        # A foreign listener must never be killed or replaced.
                        if STOP.exists() or not owner_alive(ROOT, OWNER_TOKEN):
                            break
                        for p in owned:
                            try:
                                p.terminate(); p.wait(timeout=3)
                            except psutil.NoSuchProcess:
                                pass
                            except psutil.TimeoutExpired:
                                p.kill(); p.wait(timeout=3)
                        if script=='bridge.py':
                            launch(script,'server',('--active-voices','furina','clorinde','--engine',settings()['engine'],'--port',str(settings()['port'])))
                        else:
                            launch(script,'telemetry')
                try:
                    state['simulator_controller']=simulator.ensure(state.get('bridge.py')=='healthy' and not STOP.exists() and owner_alive(ROOT, OWNER_TOKEN))
                except Exception as e:
                    state['simulator_controller']='launch_failed'
                    state['simulator_controller_error']=str(e)[:150]
                if state != previous:
                    log('state_changed',services=state)
                    previous=state
                temp=ROOT/'watchdog_status.tmp'
                temp.write_text(json.dumps(dict(time=time.strftime('%Y-%m-%d %H:%M:%S'),services=state,pid=os.getpid())),encoding='utf-8')
                temp.replace(ROOT/'watchdog_status.json')
            except Exception as e:
                log('check_error',type=type(e).__name__)
            time.sleep(2)
        STOP.write_text('desktop exited or manual stop', encoding='utf-8')
        stop_children()
        (ROOT/'watchdog_status.json').write_text(json.dumps({'state':'stopped','services':{}}),encoding='utf-8')
        log('stopped')

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--owner-token', required=True)
    main(parser.parse_args().owner_token)
