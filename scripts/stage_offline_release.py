"""Overlay public source and desktop build onto an existing offline runtime."""
import hashlib,json,shutil,subprocess,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
VERSION=json.loads((ROOT/'config/offline-bundle.json').read_text(encoding='utf-8'))['version']
target=Path(sys.argv[1]).resolve()
if not (target/'.runtime/python/python.exe').is_file():
    raise SystemExit('Clone a verified offline payload first')
files=subprocess.check_output(['git','-c','core.quotepath=false','ls-files','--cached','--others','--exclude-standard'],cwd=ROOT,text=True,encoding='utf-8').splitlines()
for name in files:
    if name=='config/default.json':continue  # Offline editions use GPT-SoVITS.
    source=ROOT/name
    if not source.is_file():continue
    destination=target/name
    destination.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source,destination)
desktop=ROOT/'dist/desktop/iracing-assistant'
shutil.copytree(desktop,target,dirs_exist_ok=True)
config=json.loads((target/'config/default.json').read_text(encoding='utf-8'))
assert config['engine']=='gpt-sovits'
assert all(not config.get(key) for key in ('documents','simulator_controller','ffmpeg'))
binding=json.loads((target/'work/race-voices/status_button_settings.json').read_text(encoding='utf-8'))
assert binding.get('device') is None and binding.get('button') is None
for name in ('local.json',):
    assert not (target/'config'/name).exists(),name
private=('driver_numbers.json','component_speech_priority.json','opening_active.json',
         'voice_library.json','voice_import_request.json','playback_settings.json','simulator_controller_owner.json','app_owner.json')
assert not any((target/'work/race-voices'/name).exists() for name in private)
manifest=[]
for name in files+['iracing-assistant.exe','config/default.json','work/race-voices/status_button_settings.json']:
    path=target/name
    if path.is_file():manifest.append({'path':name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
(target/'config/release-files.json').write_text(json.dumps({'version':VERSION,'files':manifest},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'version':VERSION,'public_files':len(manifest),'engine':config['engine'],'first_install_button':'unbound'}))
