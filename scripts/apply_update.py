"""Apply a validated, version-specific public-file patch with rollback."""
import argparse,hashlib,json,os,re,shutil,subprocess,tempfile,time,uuid,zipfile
from pathlib import Path,PurePosixPath

PRIVATE={'status_button_settings.json','playback_settings.json','tire_settings.json','emotion_settings.json','voices.json','voice_library.json','voice_import_request.json','review_state.json','review_status.json','manual_speech_active.json','app_owner.json'}
BLOCKED=('.runtime/','.updates/','复盘/','work/race-voices/models/','work/race-voices/cache/','work/race-voices/GPT-SoVITS/','work/race-voices/samples/')

def digest(data):return hashlib.sha256(data).hexdigest()

def target_path(root,name):
    if not isinstance(name,str) or not name or '\\' in name or ':' in name or '\0' in name:raise ValueError('更新路径无效')
    p=PurePosixPath(name)
    if p.is_absolute() or str(p)!=name or any(part in ('.','..') for part in p.parts):raise ValueError('更新路径越界')
    lower=name.casefold()
    reserved={'con','prn','aux','nul',*[f'com{i}' for i in range(1,10)],*[f'lpt{i}' for i in range(1,10)]}
    if any(part.endswith(('.', ' ')) or part.split('.')[0].casefold() in reserved for part in p.parts):raise ValueError('更新路径包含Windows保留名称')
    if lower in ('config/local.json','config/default.json','config/race-strategy.json') or p.name.casefold() in PRIVATE or any(lower.startswith(x.casefold()) for x in BLOCKED):raise ValueError('更新包试图覆盖个人设置或运行资源')
    if p.suffix.casefold() in ('.log','.jsonl','.pid','.lock','.wav','.mp3','.pth','.ckpt'):raise ValueError('更新包包含非程序文件')
    path=(root/name).resolve()
    if not path.is_relative_to(root):raise ValueError('更新路径通过链接越界')
    return path

def validate(root,bundle):
    root=Path(root).resolve()
    if not (root/'iracing-assistant.exe').is_file() or not (root/'.runtime/python/python.exe').is_file():raise ValueError('请选择已安装的播报助手目录')
    current=json.loads((root/'config/offline-bundle.json').read_text(encoding='utf-8-sig'))['version']
    with zipfile.ZipFile(bundle) as z:
        if len(z.infolist())>2500 or sum(i.file_size for i in z.infolist())>512*1024**2:raise ValueError('更新包大小异常')
        manifest=json.loads(z.read('manifest.json'));rows=manifest['files']
        if current not in (manifest['from_version'],manifest['to_version']):raise ValueError('更新包不适用于当前版本 '+str(current))
        for name in ('from_version','to_version'):
            if not re.fullmatch(r'\d+\.\d+\.\d+',manifest[name]):raise ValueError('更新版本无效')
        if tuple(map(int,manifest['to_version'].split('.')))<=tuple(map(int,manifest['from_version'].split('.'))):raise ValueError('不允许降级更新')
        expected=['manifest.json']+['files/'+r['path'] for r in rows]
        if len(set(n.casefold() for n in expected))!=len(expected) or sorted(z.namelist())!=sorted(expected):raise ValueError('更新包文件清单不匹配')
        changes=[]
        for row in rows:
            path=target_path(root,row['path']);data=z.read('files/'+row['path'])
            if len(data)!=row['size'] or digest(data)!=row['sha256']:raise ValueError('更新内容校验失败：'+row['path'])
            old=path.read_bytes() if path.exists() else None
            if old is not None and digest(old)==row['sha256']:continue
            if (old is None and row['base_sha256'] is not None) or (old is not None and digest(old)!=row['base_sha256']):raise ValueError('本地程序文件已有变更，未覆盖：'+row['path'])
            changes.append((row['path'],path,data,old))
    # The committed version is written last, after all program files.
    changes.sort(key=lambda item:item[0]=='config/offline-bundle.json')
    return manifest,changes

def atomic_write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    handle,name=tempfile.mkstemp(prefix='.update-',dir=path.parent)
    try:
        with os.fdopen(handle,'wb') as stream:stream.write(data)
        os.replace(name,path)
    finally:Path(name).unlink(missing_ok=True)

def apply(root,bundle,before=lambda manifest:None):
    root=Path(root).resolve();manifest,changes=validate(root,bundle)
    if not changes:return {'state':'already_current','version':manifest['to_version']}
    folder=root/'.updates'
    if not folder.resolve().is_relative_to(root):raise ValueError('备份目录通过链接越界')
    backup=folder/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]);backup.mkdir(parents=True)
    for name,path,data,old in changes:
        if old is not None:
            dest=backup/'files'/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(old)
    (backup/'transaction.json').write_text(json.dumps({'from':manifest['from_version'],'to':manifest['to_version'],'files':[{'path':n,'existed':old is not None} for n,p,d,old in changes]},ensure_ascii=False,indent=2),encoding='utf-8')
    before(manifest)
    attempted=[]
    try:
        for name,path,data,old in changes:
            attempted.append((name,path,old));atomic_write(path,data)
            if digest(path.read_bytes())!=digest(data):raise OSError('写入后校验失败：'+name)
    except Exception as error:
        failures=[]
        for name,path,old in reversed(attempted):
            try:
                if old is None:path.unlink(missing_ok=True)
                else:shutil.copyfile(backup/'files'/name,path)
            except OSError:failures.append(name)
        if failures:raise RuntimeError('更新失败，部分文件需从备份恢复：'+str(backup)+'；'+','.join(failures)) from error
        raise
    return {'state':'updated','version':manifest['to_version'],'backup':str(backup),'files':len(changes)}

def quiesce(root,manifest,wait_pid=0):
    import psutil
    exe=(root/'iracing-assistant.exe').resolve()
    if wait_pid:
        try:
            parent=psutil.Process(wait_pid)
            if Path(parent.exe()).resolve()!=exe:raise ValueError('等待退出的程序与更新目录不一致')
            parent.wait(timeout=20)
        except psutil.NoSuchProcess:pass
    was_running=False
    owned_scripts={(root/'work/race-voices'/name).resolve() for name in ('watchdog.py','bridge.py','telemetry_announcer.py')}
    for p in psutil.process_iter(['exe','cmdline']):
        try:
            if p.info['exe'] and Path(p.info['exe']).resolve()==exe:raise ValueError('请先从托盘退出播报助手，再继续更新')
            if p.info['exe'] and Path(p.info['exe']).resolve()==(root/'.runtime/python/python.exe').resolve():
                was_running |= any(Path(arg).resolve() in owned_scripts for arg in (p.info['cmdline'] or [])[1:] if not arg.startswith('-'))
        except (psutil.NoSuchProcess,psutil.AccessDenied):pass
    status=root/'work/race-voices/telemetry_status.json'
    if status.exists() and time.time()-status.stat().st_mtime<6 and json.loads(status.read_text()).get('state')=='connected':raise ValueError('请先退出驾驶会话，再更新')
    for name,allowed in manifest.get('controls',{}).items():
        if digest((root/name).read_bytes()) not in allowed:raise ValueError('服务管理文件已有变更，未执行：'+name)
    required={'work/race-voices/stop_services.py','work/race-voices/watchdog.py','scripts/start.ps1'}
    if not required.issubset(manifest.get('controls',{})):raise ValueError('更新包缺少服务管理校验')
    subprocess.run([str(root/'.runtime/python/python.exe'),'-X','utf8',str(root/'work/race-voices/stop_services.py')],cwd=root/'work/race-voices',check=True,timeout=35,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return was_running

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--target',required=True);parser.add_argument('--bundle',required=True);parser.add_argument('--check',action='store_true');parser.add_argument('--wait-pid',type=int,default=0);parser.add_argument('--result-file');args=parser.parse_args()
    root=Path(args.target).resolve();code=0;running=[False]
    def before(manifest):running[0]=quiesce(root,manifest,args.wait_pid)
    try:
        if args.check:
            manifest,changes=validate(root,args.bundle);message='更新文件验证通过。'
        else:
            result=apply(root,args.bundle,before=before);message=('已是最新版本。' if result['state']=='already_current' else '更新完成。备份目录：'+result['backup'])
    except Exception as error:code=1;message='更新未完成：'+str(error)+'。请查看备份或重新运行更新器。'
    # New deployments bind services to the desktop; the installer reopens it.
    # Preserve legacy restoration only when updating an older deployment.
    if running[0] and not (root/'work/race-voices/app_lifecycle.py').exists():
        try:subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(root/'scripts/start.ps1')],cwd=root/'work/race-voices',check=True,timeout=40,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except Exception:message+=' 后台未能自动恢复，请在助手首页启动播报。'
    if not code and not args.check and os.name=='nt':
        try:
            import winreg
            key=r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{C6EF8023-4883-4F24-A134-043E8D091F09}_is1'
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,key,0,winreg.KEY_READ|winreg.KEY_SET_VALUE) as k:
                installed=winreg.QueryValueEx(k,'InstallLocation')[0]
                if Path(installed).resolve()==root:winreg.SetValueEx(k,'DisplayVersion',0,winreg.REG_SZ,result['version'])
        except OSError:pass
    if args.result_file:Path(args.result_file).write_text(message,encoding='utf-8-sig')
    print(message);return code

if __name__=='__main__':raise SystemExit(main())
