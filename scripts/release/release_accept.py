"""Run the real installer or updaters in isolated folders and check personal settings survive.

Usage: python release_accept.py 0.3.0 full
       python release_accept.py 0.3.0 patches --from 0.2.4 0.2.5 0.2.6 0.2.7 0.2.8
"""
from pathlib import Path
import os,json,hashlib,shutil,subprocess
from release_common import build_dir,payload,installer,updater as updater_exe,parser
cli=parser(__doc__);cli.add_argument('mode',nargs='?',choices=('full','patches'),default='patches')
cli.add_argument('--from',dest='olds',nargs='+',default=[],metavar='OLD')
args=cli.parse_args();VERSION=args.version
if args.mode=='patches' and not args.olds:cli.error('patches mode needs --from')
BUILD=build_dir(VERSION);FULL=installer(VERSION)
KEY=r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{C6EF8023-4883-4F24-A134-043E8D091F09}_is1'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def run(args,timeout=120):
    result=subprocess.run([str(a) for a in args],capture_output=True,timeout=timeout)
    if result.returncode:raise RuntimeError(str(args[0])+' exit '+str(result.returncode)+' '+result.stderr.decode(errors='replace')[-500:])
    return result

def markers(target,port):
    values={'config/local.json':json.dumps(dict(port=port,documents=str(target/'Documents'),simulator_controller=str(target/'no-controller'))).encode(),
            'work/race-voices/status_button_settings.json':b'{"device":2,"button":8,"cooldown_seconds":9}',
            'work/race-voices/playback_settings.json':b'{"volume_percent":73}',
            'config/race-strategy.json':b'{"enabled":false,"private_marker":"preserve"}',
            'work/race-voices/cache/private.wav':b'private cache marker',
            'work/race-voices/review_state.json':b'{"schema":1,"current":null,"pending":[],"done":[],"private_marker":true}',
            '复盘/private.txt':'私人复盘保留测试'.encode()}
    for name in ('emotion_settings.json','tire_settings.json','voices.json'):
        path=target/'work/race-voices'/name;row=json.loads(path.read_text(encoding='utf-8-sig'))
        if name=='voices.json':row['furina']['private_marker']='preserve'
        else:row['private_marker']='preserve'
        values['work/race-voices/'+name]=json.dumps(row,ensure_ascii=False).encode()
    for name,data in values.items():
        p=target/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    return {name:sha(target/name) for name in values}

def check_markers(target,values):
    for name,digest in values.items():assert sha(target/name)==digest,('personal settings overwritten',name)

def smoke(target):
    result=subprocess.run([str(target/'iracing-assistant.exe'),'--project-dir',str(target),'--smoke-test'],cwd=target,env={**os.environ,'QT_QPA_PLATFORM':'offscreen'},capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr.decode(errors='replace')
    return result.returncode

def full_install():
    import winreg
    target=BUILD/'acceptance-full-final';before=None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,KEY) as key:
            before={}
            for i in range(winreg.QueryInfoKey(key)[1]):
                name,value,kind=winreg.EnumValue(key,i);before[name]=(value,kind)
    except FileNotFoundError:pass
    report={}
    try:
        args=[FULL,'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-','/NOICONS','/TASKS=','/DIR='+str(target)]
        print('Running actual complete installer in isolated directory',flush=True)
        run(args+['/LOG='+str(BUILD/'acceptance-full.log')],900)
        manifest=json.loads((target/'config/release-files.json').read_text(encoding='utf-8'))
        assert manifest['version']==VERSION
        assert all(sha(target/r['path'])==r['sha256'] for r in manifest['files'])
        binding=json.loads((target/'work/race-voices/status_button_settings.json').read_text())
        assert binding['device'] is None and binding['button'] is None
        assert not (target/'config/local.json').exists() and not (target/'复盘').exists()
        first_smoke=smoke(target)
        protected=markers(target,19987)
        print('Testing complete-installer overwrite preserves personal settings',flush=True)
        run(args+['/LOG='+str(BUILD/'acceptance-full-overwrite.log')],900)
        check_markers(target,protected)
        report=dict(actual_installer_exit=0,version=VERSION,fresh_files_verified=len(manifest['files']),first_install_button='unbound',fresh_exe_smoke=first_smoke,overwrite_preserved=len(protected),overwrite_exe_smoke=smoke(target))
    finally:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,KEY,0,winreg.KEY_READ|winreg.KEY_SET_VALUE) as key:
            location=winreg.QueryValueEx(key,'InstallLocation')[0]
            assert Path(location).resolve()==target.resolve(),'Unexpected uninstall registration; not restoring blindly'
            if before is not None:
                current=[winreg.EnumValue(key,i)[0] for i in range(winreg.QueryInfoKey(key)[1])]
                for name in current:
                    if name not in before:winreg.DeleteValue(key,name)
                for name,(value,kind) in before.items():winreg.SetValueEx(key,name,0,kind,value)
        if before is None:winreg.DeleteKey(winreg.HKEY_CURRENT_USER,KEY)
    report['existing_registration_restored']=True
    return report

def patch_install(version,index):
    base=payload(version);target=BUILD/('acceptance-final-'+version)
    def ignore(folder,names):
        if Path(folder).relative_to(base).as_posix()=='work/race-voices':return [n for n in names if n in ('models','GPT-SoVITS','nltk_data') or n=='__pycache__']
        return [n for n in names if n=='__pycache__' or n.endswith('.pyc')]
    def copy(src,dest):
        if Path(src).relative_to(base).as_posix().startswith('.runtime/'):os.link(src,dest)
        else:shutil.copy2(src,dest)
        return dest
    print('Preparing updater acceptance '+version,flush=True)
    shutil.copytree(base,target,copy_function=copy,ignore=ignore)
    for name in ('models','GPT-SoVITS','nltk_data'):
        script="New-Item -ItemType Junction -Path '"+str(target/'work/race-voices'/name)+"' -Target '"+str(base/'work/race-voices'/name)+"' | Out-Null"
        run(['powershell','-NoProfile','-Command',script])
    protected=markers(target,19984+index)
    updater=updater_exe(VERSION,version)
    run([updater,'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-','/DIR='+str(target),'/LOG='+str(BUILD/('acceptance-'+version+'.log'))])
    assert json.loads((target/'config/offline-bundle.json').read_text())['version']==VERSION
    manifest=json.loads((BUILD/('patch-'+version)/'manifest.json').read_text(encoding='utf-8'))
    assert all(sha(target/r['path'])==r['sha256'] for r in manifest['files'])
    check_markers(target,protected)
    return dict(from_version=version,actual_updater_exit=0,files_verified=len(manifest['files']),personal_markers_preserved=len(protected),exe_smoke=smoke(target))

mode=args.mode
if mode=='full':report=full_install()
else:report=[patch_install(version,i) for i,version in enumerate(args.olds)]
(BUILD/('acceptance-'+mode+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)