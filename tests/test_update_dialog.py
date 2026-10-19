import os,sys,tempfile,unittest
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import Future
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from PySide6.QtWidgets import QApplication
from update_dialog import UpdateDialog

class DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.window=UpdateDialog(SimpleNamespace(project=Path(self.temp.name)),auto=False)
    def tearDown(self):self.window.future=None;self.window.close()
    def result(self,value,kind='check'):
        self.window.kind=kind;self.window.future=Future();self.window.future.set_result(value);self.window.tick()
    def test_matching_patch_enables_download(self):
        self.result(dict(state='available',latest='0.2.6',notes='更新说明',asset={'size':1048576}))
        self.assertTrue(self.window.install_button.isEnabled());self.assertIn('1.0 MB',self.window.status.text())
    def test_missing_patch_does_not_offer_full_package_as_increment(self):
        self.result(dict(state='available',latest='0.2.6',notes='',asset=None))
        self.assertFalse(self.window.install_button.isEnabled());self.assertIn('不下载完整安装包',self.window.status.text())
    def test_driving_blocks_installation(self):
        self.window.release={'asset':{'size':1}};self.window.can_apply=lambda:False
        self.window.install();self.assertIsNone(self.window.future);self.assertIn('驾驶会话',self.window.status.text())
    def test_verified_download_starts_installer_before_quitting(self):
        self.window.on_launch=Mock();path=Path(self.temp.name)/'updater.exe'
        with patch('update_dialog.subprocess.Popen') as launch:self.result(path,'download')
        launch.assert_called_once();self.assertIn('/DIR='+self.temp.name,launch.call_args.args[0]);self.window.on_launch.assert_called_once()
    def test_network_failure_does_not_start_anything(self):
        self.window.kind='check';self.window.future=Future();self.window.future.set_exception(RuntimeError('offline'))
        with patch('update_dialog.subprocess.Popen') as launch:self.window.tick()
        launch.assert_not_called();self.assertIn('当前播报不受影响',self.window.status.text())

if __name__=='__main__':unittest.main()
