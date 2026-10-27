"""Start an installed build, wait for healthy services, quit and confirm nothing is left running.

Usage: python release_lifecycle.py 0.3.0 D:\\path\\to\\installed\\copy
"""
import json,subprocess,time
from pathlib import Path
import psutil,requests
from release_common import parser
cli=parser(__doc__);cli.add_argument('root');args=cli.parse_args()
root=Path(args.root).resolve();exe=root/'iracing-assistant.exe';voice=root/'work/race-voices'
settings=json.loads((root/'config/local.json').read_text());port=settings['port']
def alive_children():
 out=[]
 for p in psutil.process_iter(['exe','cmdline']):
  try:
   if p.info['exe'] and Path(p.info['exe']).resolve()==exe:out.append(p.pid)
   elif any(str((voice/n).resolve()).casefold()==a.casefold() for n in ('watchdog.py','bridge.py','telemetry_announcer.py') for a in (p.info['cmdline'] or [])[1:]):out.append(p.pid)
  except (psutil.Error,OSError):pass
 return out
def wait(test,timeout):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  if test():return
  time.sleep(.4)
 raise RuntimeError('Timeout: '+str(alive_children()))
def ready():
 try:
  data=requests.get(f'http://127.0.0.1:{port}/health',timeout=1).json()
  owner=json.loads((voice/'app_owner.json').read_text())
  state=json.loads((voice/'watchdog_status.json').read_text())
  return data.get('status')=='ready' and owner['token'] in psutil.Process(state['pid']).cmdline() and state.get('services',{}).get('telemetry_announcer.py')=='healthy'
 except Exception:return False
assert not alive_children()
app=subprocess.Popen([str(exe),'--project-dir',str(root)],cwd=root,env={**__import__('os').environ,'QT_QPA_PLATFORM':'offscreen'},creationflags=0x08000000)
try:
 wait(ready,90)
 subprocess.run([str(exe),'--project-dir',str(root),'--quit'],cwd=root,env={**__import__('os').environ,'QT_QPA_PLATFORM':'offscreen'},timeout=20,check=True,creationflags=0x08000000)
 wait(lambda:not alive_children(),40)
 report=dict(version=args.version,normal_start='ready',normal_exit='no_owned_processes',port_isolated=True)
 (root.parent/('lifecycle-'+root.name+'.json')).write_text(json.dumps(report,indent=2),encoding='utf-8')
 print(json.dumps(report))
finally:
 if alive_children():
  if app.poll() is None:app.kill();app.wait(timeout=10)
  subprocess.run([str(root/'.runtime/python/python.exe'),str(voice/'stop_services.py')],cwd=voice,timeout=40,creationflags=0x08000000)
