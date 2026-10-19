import hashlib,importlib.util,io,json,sys,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'));sys.path.insert(0,str(ROOT/'scripts'))
try:
    import update_client
    import apply_update
except ImportError:
    update_client=apply_update=None

def sha(data):return hashlib.sha256(data).hexdigest()

class PatchTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(apply_update,'Transactional updater is missing')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'old app';self.root.mkdir()
        self.write('iracing-assistant.exe',b'MZfixture')
        self.write('.runtime/python/python.exe',b'MZruntime')
        self.write('config/offline-bundle.json',b'{"version":"0.2.4"}')
        self.write('work/race-voices/bridge.py',b'old code')

    def write(self,name,data):
        p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);return p

    def bundle(self,extra=None):
        rows=[('work/race-voices/bridge.py',b'new code',sha(b'old code')),
              ('work/race-voices/new_module.py',b'new module',None),
              ('config/offline-bundle.json',b'{"version":"0.2.6"}',sha(b'{"version":"0.2.4"}'))]
        if extra:rows.append(extra)
        file=Path(self.tmp.name)/'patch.zip'
        manifest={'from_version':'0.2.4','to_version':'0.2.6','files':[dict(path=n,sha256=sha(d),base_sha256=b,size=len(d)) for n,d,b in rows]}
        with zipfile.ZipFile(file,'w') as z:
            z.writestr('manifest.json',json.dumps(manifest))
            for n,d,b in rows:z.writestr('files/'+n,d)
        return file

    def test_update_preserves_user_settings_models_and_cache(self):
        private=['config/local.json','work/race-voices/status_button_settings.json','work/race-voices/playback_settings.json','work/race-voices/voice_library.json','work/race-voices/models/custom/x.pth','work/race-voices/cache/x.wav']
        for name in private:self.write(name,b'private data')
        result=apply_update.apply(self.root,self.bundle())
        self.assertEqual((self.root/'work/race-voices/bridge.py').read_bytes(),b'new code')
        self.assertEqual(json.loads((self.root/'config/offline-bundle.json').read_text())['version'],'0.2.6')
        for name in private:self.assertEqual((self.root/name).read_bytes(),b'private data')
        self.assertEqual((Path(result['backup'])/'files/work/race-voices/bridge.py').read_bytes(),b'old code')

    def test_repeat_does_not_create_backup_or_change_files(self):
        bundle=self.bundle();apply_update.apply(self.root,bundle)
        before=list((self.root/'.updates').rglob('*'))
        self.assertEqual(apply_update.apply(self.root,bundle)['state'],'already_current')
        self.assertEqual(list((self.root/'.updates').rglob('*')),before)

    def test_wrong_version_and_modified_code_are_rejected_before_stop(self):
        bundle=self.bundle();self.write('config/offline-bundle.json',b'{"version":"0.1.0"}')
        hook=Mock()
        with self.assertRaises(ValueError):apply_update.apply(self.root,bundle,before=hook)
        hook.assert_not_called()
        self.write('config/offline-bundle.json',b'{"version":"0.2.4"}');self.write('work/race-voices/bridge.py',b'user modified code')
        with self.assertRaises(ValueError):apply_update.apply(self.root,bundle,before=hook)
        hook.assert_not_called()

    def test_traversal_and_private_paths_are_rejected(self):
        for name in ('../outside','C:/outside','config/local.json','config/race-strategy.json','config/RACE-STRATEGY.JSON','复盘/history.txt','work/race-voices/review_state.json','work/race-voices/local_playback.json','work/race-voices/review_native_events.JSONL','work/race-voices/voices.json','work/race-voices/VOICES.JSON','work/race-voices/cache/x.wav','.runtime/python/x.py','work/CON','work/file.'):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):apply_update.apply(self.root,self.bundle((name,b'evil',None)))
        self.assertFalse((self.root.parent/'outside').exists())

    def test_corrupt_archive_fails_without_mutation(self):
        bundle=self.bundle()
        with zipfile.ZipFile(bundle,'a') as z:z.writestr('unexpected',b'bad')
        with self.assertRaises(ValueError):apply_update.apply(self.root,bundle)
        self.assertEqual((self.root/'work/race-voices/bridge.py').read_bytes(),b'old code')

    def test_copy_failure_rolls_back_and_removes_new_files(self):
        bundle=self.bundle();original=apply_update.atomic_write
        def fail_metadata(path,data):
            if path.name=='offline-bundle.json':raise OSError('simulated full disk')
            original(path,data)
        with patch.object(apply_update,'atomic_write',side_effect=fail_metadata):
            with self.assertRaises(OSError):apply_update.apply(self.root,bundle)
        self.assertEqual((self.root/'work/race-voices/bridge.py').read_bytes(),b'old code')
        self.assertFalse((self.root/'work/race-voices/new_module.py').exists())
        self.assertEqual(json.loads((self.root/'config/offline-bundle.json').read_text())['version'],'0.2.4')

class ClientTests(unittest.TestCase):
    def test_check_searches_next_page_and_sorts_compatible_versions(self):
        newer=dict(self.release(),tag_name='v0.2.7',assets=[])
        first=Mock();first.json.return_value=[newer]*100
        second=Mock();second.json.return_value=[self.release()]
        with tempfile.TemporaryDirectory() as d,patch('update_client.requests.get',side_effect=[first,second]) as request:
            root=Path(d);(root/'config').mkdir();(root/'config/offline-bundle.json').write_text('{"version":"0.2.4"}')
            result=update_client.check(root)
        self.assertEqual(result['latest'],'0.2.6');self.assertEqual(result['newest_release'],'0.2.7')
        self.assertEqual(request.call_args_list[1].kwargs['params']['page'],2)

    def test_check_finds_compatible_patch_when_latest_release_has_only_full_installer(self):
        newer=dict(self.release(),tag_name='v0.2.7',assets=[dict(name='iracing-chinese-assistant-0.2.7-setup.exe',size=9)])
        response=Mock();response.json.return_value=[newer,self.release()]
        with tempfile.TemporaryDirectory() as d,patch('update_client.requests.get',return_value=response):
            root=Path(d);(root/'config').mkdir();(root/'config/offline-bundle.json').write_text('{"version":"0.2.4"}')
            result=update_client.check(root)
        self.assertEqual(result['latest'],'0.2.6');self.assertIsNotNone(result['asset'])

    def test_download_rejects_full_installer_even_when_called_directly(self):
        asset=dict(self.release()['assets'][0],name='iracing-chinese-assistant-0.2.6-setup.exe')
        with tempfile.TemporaryDirectory() as d,patch('update_client.requests.get') as request:
            with self.assertRaises(ValueError):update_client.download(asset,Path(d))
        request.assert_not_called()

    def test_check_ignores_preview_releases_and_uses_highest_compatible_version(self):
        newest=dict(self.release(),tag_name='v0.2.9',prerelease=True)
        response=Mock();response.json.return_value=[self.release(),newest]
        with tempfile.TemporaryDirectory() as d,patch('update_client.requests.get',return_value=response):
            result=update_client.check(Path(d))
        # This installation has no version metadata; do not invent a matching patch.
        self.assertIsNone(result['asset']);self.assertEqual(result['latest'],'0.2.6')
    def setUp(self):self.assertIsNotNone(update_client,'Online update client is missing')

    def release(self):
        return dict(tag_name='v0.2.6',draft=False,prerelease=False,body='说明',html_url='https://github.com/honokaoshi83-max/iracing-chinese-assistant/releases/tag/v0.2.6',assets=[dict(name='iracing-chinese-assistant-0.2.6-update-from-0.2.4.exe',size=4,digest='sha256:'+sha(b'data'),browser_download_url='https://github.com/honokaoshi83-max/iracing-chinese-assistant/releases/download/v0.2.6/update.exe')])

    def test_version_comparison_and_matching_patch(self):
        result=update_client.select_release(self.release(),'0.2.4')
        self.assertEqual(result['state'],'available');self.assertIsNotNone(result['asset'])
        self.assertEqual(update_client.select_release(self.release(),'0.2.6')['state'],'current')
        self.assertEqual(update_client.select_release(self.release(),'0.2.7')['state'],'current')
        self.assertIsNone(update_client.select_release(self.release(),'0.2.5')['asset'])

    def test_untrusted_or_unchecked_asset_is_not_accepted(self):
        for key,value in [('browser_download_url','https://evil.example/update.exe'),('digest',None)]:
            r=self.release();r['assets'][0][key]=value
            with self.assertRaises(ValueError):update_client.select_release(r,'0.2.4')

    def test_download_is_verified_before_publishing_file(self):
        asset=update_client.select_release(self.release(),'0.2.4')['asset']
        response=Mock();response.url=asset['browser_download_url'];response.iter_content.return_value=[b'data']
        with tempfile.TemporaryDirectory() as tmp,patch('update_client.requests.get',return_value=response):
            path=update_client.download(asset,Path(tmp));self.assertEqual(path.read_bytes(),b'data')
            response.iter_content.return_value=[b'evil']
            with self.assertRaises(ValueError):update_client.download(asset,Path(tmp))
            self.assertEqual(path.read_bytes(),b'data');self.assertFalse(list(Path(tmp).glob('*.part')))

if __name__=='__main__':unittest.main()
