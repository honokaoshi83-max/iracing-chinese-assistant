"""Verify source hashes, model integrity and first-install privacy in a payload."""
import hashlib,json,sys
from pathlib import Path

root=Path(sys.argv[1]).resolve()
manifest=json.loads((root/'config/release-files.json').read_text(encoding='utf-8'))
assert json.loads((root/'config/offline-bundle.json').read_text(encoding='utf-8'))['version']==manifest['version']
for row in manifest['files']:
    path=root/row['path']
    assert hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256'],row['path']
assets=json.loads((root/'config/assets.json').read_text(encoding='utf-8'))
for row in assets:
    path=root/'work/race-voices'/row['path']
    assert path.is_file(),row['path']
    assert path.stat().st_size==row['size'],row['path']
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b''):digest.update(block)
    assert digest.hexdigest()==row['sha256'],row['path']
assert not (root/'复盘').exists(),'个人复盘目录'
for name in ('local.json','race-strategy.json'):assert not (root/'config'/name).exists(),name
for name in ('driver_numbers.json','component_speech_priority.json','opening_active.json',
             'voice_library.json','voice_import_request.json','playback_settings.json','simulator_controller_owner.json','app_owner.json','review_state.json','review_status.json','manual_speech_active.json'):
    assert not (root/'work/race-voices'/name).exists(),name
print(json.dumps({'version':manifest['version'],'source_hashes':len(manifest['files']),
                  'verified_models':len(assets),'private_state':'absent'}))
