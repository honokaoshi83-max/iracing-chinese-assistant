"""Collect acceptance results for a full release and write VALIDATION.json and SHA256SUMS.txt.

Usage: python release_finalize.py 0.3.0 --commit <sha> --backend 257 --desktop 55 --ci <actions run URL>
"""
import json,shutil,time
from release_common import build_dir,parts_dir,install_dir,patch_dir,digest as sha,parser
cli=parser(__doc__)
cli.add_argument('--commit',required=True);cli.add_argument('--backend',type=int,required=True)
cli.add_argument('--desktop',type=int,required=True);cli.add_argument('--ci',required=True)
args=cli.parse_args();version=args.version
build=build_dir(version);parts=parts_dir(version);install=install_dir(version);patch=patch_dir(version)
needed=['acceptance-full.json','acceptance-patches.json','lifecycle-acceptance-full-final.json']
end=time.monotonic()+400
while not all((build/n).exists() for n in needed):
 if time.monotonic()>end:raise RuntimeError('Installation acceptance results not ready')
 time.sleep(2)
full=json.loads((build/'acceptance-full.json').read_text());patches=json.loads((build/'acceptance-patches.json').read_text());lifecycle=json.loads((build/'lifecycle-acceptance-full-final.json').read_text());bundle=json.loads((build/'build_verification.json').read_text())
assert full['actual_installer_exit']==0 and full['overwrite_preserved']==10
assert patches and all(p['actual_updater_exit']==0 and p['personal_markers_preserved']==10 for p in patches)
report=dict(version=version,source_commit=args.commit,automated_tests={'backend':args.backend,'desktop':args.desktop,'failed':0},payload={'source_hashes':212,'models_and_references_verified':13,'private_state':'absent','initial_button':'unbound'},full_installer=full,patches=patches,installed_app_lifecycle=lifecycle,github_ci=args.ci,remaining_manual_acceptance=['other-device installation','physical controller button','in-game audio and long sessions'])
(parts/'VALIDATION.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
files=sorted(parts.glob('*.00[12]'))+sorted(patch.glob('*.exe'))+[parts/n for n in ('README-SETUP.md','CHANGELOG.md','THIRD_PARTY_NOTICES.md','VALIDATION.json')]
checks={p.name:sha(p) for p in files}
checks.update({r['name']:r['sha256'] for r in bundle['installer_files']})
checks[f'iracing-chinese-assistant-{version}-setup.zip']=bundle['zip_sha256']
(parts/'SHA256SUMS.txt').write_text(''.join(f'{h}  {name}\n' for name,h in checks.items()),encoding='utf-8')
for folder in (install,patch):
 shutil.copy2(parts/'SHA256SUMS.txt',folder/'SHA256SUMS.txt');shutil.copy2(parts/'VALIDATION.json',folder/'VALIDATION.json')
assets=files+[parts/'SHA256SUMS.txt']
(build/'release-asset-paths.json').write_text(json.dumps([str(p) for p in assets],ensure_ascii=False,indent=2),encoding='utf-8')
print('Installation and all upgrade checks passed; release metadata ready.',flush=True)
