"""Check every uploaded GitHub release asset against the local files by size and SHA-256.

Usage: python release_verify_github.py 0.3.0
"""
import json,subprocess
from pathlib import Path
from release_common import build_dir,digest,parser
version=parser(__doc__).parse_args().version
build=build_dir(version)
assets=[Path(p) for p in json.loads((build/'release-asset-paths.json').read_text())]
r=json.loads(subprocess.check_output(['gh','release','view','v'+version,'--json','tagName,isDraft,assets,url'],text=True,encoding='utf-8'))
remote={a['name']:a for a in r['assets']}
assert set(remote)=={p.name for p in assets},('asset list mismatch',set(remote),{p.name for p in assets})
rows=[]
for p in assets:
 h=digest(p)
 a=remote[p.name]
 assert a['size']==p.stat().st_size,p.name
 assert a['digest']=='sha256:'+h,p.name
 assert a['state']=='uploaded',p.name
 rows.append(dict(name=p.name,bytes=a['size'],sha256=h,verified=True))
report=dict(version=version,draft=r['isDraft'],assets_verified=len(rows),assets=rows)
(build/'github_verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(dict(version=version,assets_verified=len(rows),draft=r['isDraft'])))
