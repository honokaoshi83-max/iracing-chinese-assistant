"""Build and verify a new offline installer without modifying older releases.

Usage: python release_build.py 0.3.0 --base 0.2.8
"""
import hashlib,json,shutil,subprocess,sys,zipfile
from release_common import SOURCE,ISCC,build_dir,payload,install_dir,parts_dir,digest,parser

args=parser(__doc__,base=True).parse_args()
VERSION=args.version
BASE=payload(args.base)
BUILD=build_dir(VERSION)
PAYLOAD=BUILD/'payload'
INSTALL=install_dir(VERSION)
PARTS=parts_dir(VERSION)
BUILD.mkdir(exist_ok=False);INSTALL.mkdir(parents=True,exist_ok=False);PARTS.mkdir(parents=True,exist_ok=False)

def run(args,**kwargs):subprocess.run([str(a) for a in args],check=True,**kwargs)

print(f'Cloning the verified {args.base} offline runtime into a new payload',flush=True)
shutil.copytree(BASE,PAYLOAD,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
run([sys.executable,'-X','utf8',SOURCE/'scripts/stage_offline_release.py',PAYLOAD],cwd=SOURCE)
run([sys.executable,'-X','utf8',SOURCE/'scripts/validate_offline_release.py',PAYLOAD],cwd=SOURCE)
for name in ('simulator_identity.py','install_simulator.py','slow_car_settings.py','simulator_link.py','opening_sequence.py','voice_routing.py','damage_monitor.py','flag_monitor.py'):
    assert digest(SOURCE/'work/race-voices'/name)==digest(PAYLOAD/'work/race-voices'/name),name
assert not (PAYLOAD/'work/race-voices/cache').exists()
assert (PAYLOAD/'data/audio/status-ack.wav').stat().st_size>1000
print(f'Compiling the complete {VERSION} installer',flush=True)
with (BUILD/'compiler.log').open('wb') as log:
    run([ISCC,'/Q',f'/DPayloadDir={PAYLOAD}',f'/DOutputDir={INSTALL}',SOURCE/'scripts/desktop-setup.iss'],stdout=log,stderr=subprocess.STDOUT)
for src,dest in [(f'docs/SETUP_{VERSION}.md','README-SETUP.md'),(f'docs/CHANGELOG_{VERSION}.md','CHANGELOG.md'),('THIRD_PARTY.md','THIRD_PARTY_NOTICES.md')]:shutil.copy2(SOURCE/src,INSTALL/dest)
installers=sorted(INSTALL.glob('*.exe'))+sorted(INSTALL.glob('*.bin'))
assert len(installers)==2,installers
checks={p.name:digest(p) for p in installers}
(INSTALL/'SHA256SUMS.txt').write_text(''.join(f'{h}  {name}\n' for name,h in checks.items()),encoding='utf-8')
archive=BUILD/f'iracing-chinese-assistant-{VERSION}-setup.zip'
print('Packing and checking the full ZIP',flush=True)
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as z:
    for path in sorted(INSTALL.iterdir()):
        if path.is_file():z.write(path,path.name)
with zipfile.ZipFile(archive) as z:assert z.testzip() is None
print('Creating GitHub download volumes',flush=True)
part_files=[]
with archive.open('rb') as stream:
    while stream.tell()<archive.stat().st_size:
        target=PARTS/f'{archive.name}.{len(part_files)+1:03d}';remaining=1900*1024*1024
        with target.open('wb') as f:
            while remaining:
                block=stream.read(min(4*1024*1024,remaining))
                if not block:break
                f.write(block);remaining-=len(block)
        part_files.append(target)
combined=hashlib.sha256()
for path in part_files:
    with path.open('rb') as f:
        for block in iter(lambda:f.read(4*1024*1024),b''):combined.update(block)
archive_hash=digest(archive);assert combined.hexdigest()==archive_hash
checks.update({p.name:digest(p) for p in part_files})
for name in ('README-SETUP.md','CHANGELOG.md','THIRD_PARTY_NOTICES.md'):shutil.copy2(INSTALL/name,PARTS/name)
(PARTS/'SHA256SUMS.txt').write_text(''.join(f'{h}  {name}\n' for name,h in checks.items()),encoding='utf-8')
notes=(SOURCE/f'docs/SETUP_{VERSION}.md').read_text(encoding='utf-8')+'\n---\n\n'+(SOURCE/f'docs/CHANGELOG_{VERSION}.md').read_text(encoding='utf-8')
(BUILD/'release-notes.md').write_text(notes,encoding='utf-8')
report={'version':VERSION,'installer_files':[{'name':p.name,'bytes':p.stat().st_size,'sha256':checks[p.name]} for p in installers],'zip_bytes':archive.stat().st_size,'zip_sha256':archive_hash,'zip_test':'passed','combined_parts_hash':'matched','parts':[{'name':p.name,'bytes':p.stat().st_size,'sha256':checks[p.name]} for p in part_files],'private_state':'absent','initial_button':'unbound'}
(BUILD/'build_verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
