import os, sys, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
os.environ['QT_QPA_PLATFORM']='offscreen'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from PySide6.QtWidgets import QApplication, QMainWindow, QLabel
from main import Window
from app_lifecycle import claim, owner_alive


class LifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_exit_releases_owner_before_async_stop(self):
        with tempfile.TemporaryDirectory() as d:
            window=QMainWindow()
            window.closing=False;window.job=None
            window.timer=Mock();window.pool=Mock();window.tray=Mock()
            window.footer=QLabel();window.backend=SimpleNamespace(root=Path(d),python=Path(sys.executable))
            window.service_owner=claim(d);window.finish_quit=Mock()
            with patch('main.QProcess') as process, patch('main.QTimer.singleShot'):
                Window.quit_ui(window)
                self.assertFalse(owner_alive(d))
                process.return_value.start.assert_called_once_with(str(sys.executable),['-X','utf8',str(Path(d)/'stop_services.py')])
                window.finish_quit.assert_not_called()
                Window.quit_ui(window)
                process.assert_called_once()
            window.deleteLater()

    def test_close_to_tray_keeps_owner(self):
        with tempfile.TemporaryDirectory() as d:
            window=SimpleNamespace(closing=False,hide=Mock(),tray=Mock())
            record=claim(d);event=Mock()
            Window.closeEvent(window,event)
            event.ignore.assert_called_once();window.hide.assert_called_once()
            self.assertTrue(owner_alive(d))


if __name__=='__main__':unittest.main()
