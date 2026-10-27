"""Build one update patch per older installed version from clean payloads.

Usage: python release_patches.py 0.3.0 --from 0.2.4 0.2.5 0.2.6 0.2.7 0.2.8
"""
import sys,subprocess,json,shutil
from release_common import SOURCE as source,ISCC,build_dir,payload,patch_dir,parser
args=parser(__doc__,olds=True).parse_args();version=args.version
build=build_dir(version);target=build/'payload'
sys.path.insert(0,str(source/'scripts'))
from build_delta import build as delta
out=patch_dir(version);out.mkdir(parents=True,exist_ok=True)
reports=[]
for old in args.olds:
    patch=build/('patch-'+old)
    result=delta(payload(old),target,patch)
    with (patch/'compiler.log').open('wb') as log:
        subprocess.run([str(ISCC),'/Q','/DPatchDir='+str(patch),'/DFromVersion='+old,'/DToVersion='+version,'/DOutputDir='+str(out),str(source/'scripts/update-setup.iss')],check=True,stdout=log,stderr=subprocess.STDOUT)
    reports.append(result)
print(json.dumps(reports,ensure_ascii=False))
(build/'patch-builds.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')
# Patch-only releases ship UPDATE_<version>.md; full releases reuse SETUP_<version>.md.
notes=source/f'docs/UPDATE_{version}.md'
shutil.copy2(notes if notes.exists() else source/f'docs/SETUP_{version}.md',out/'README-UPDATE.md')
